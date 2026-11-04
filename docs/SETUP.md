# Run Aegis on your machine

Start here after cloning the repository. The main demo needs one computer,
one recording or webcam, and a Meta Model API key. It does not need a GPU,
Docker, a separate database server, door sensors or a phone.

## 1. Install the prerequisites

| Tool | Version / requirement |
|---|---|
| [Python](https://www.python.org/downloads/) | **CPython 3.13, 64-bit**, with venv and pip. The requirements were pinned for this Python version. |
| [Node.js](https://nodejs.org/en/download) | **22 or 24 LTS**, including npm. CI uses 22; the recorded local web build used 24. |
| [ffmpeg](https://ffmpeg.org/download.html) | Both `ffmpeg` and `ffprobe` must be on `PATH`. A build with the `libx264` encoder is needed for event clips. |
| Browser | Current Chrome or Edge. Use the same browser and origin for setup and monitoring. |
| Disk / network | Allow several GB for PyTorch, the environment and build caches. Installation downloads packages and weights; hosted Meta calls need internet access. |

**Windows:** install Python 3.13 from Python.org, Node LTS from Nodejs.org, and
a Windows ffmpeg build linked from ffmpeg.org. Add ffmpeg's `bin` directory to
your user `PATH`. Open a new PowerShell window, then check:

```powershell
py -3.13 --version
node --version
npm.cmd --version
ffmpeg -version
ffprobe -version
```

**Debian / Ubuntu:** install Python 3.13 and Node 22/24 using your preferred
package source. Check `python3.13 -m venv --help`; if your distribution splits
venv into its own package, install the matching `python3.13-venv` package too.
The native libraries used by the app are:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libportaudio2 libgl1 espeak-ng
python3.13 --version
node --version
npm --version
```

**macOS:** install Python 3.13, Node 22/24, ffmpeg and PortAudio. The runner has
macOS paths and the requirements have macOS PyTorch entries, but this project
has not had a recorded macOS install run. Windows is the locally exercised
path; the repository also defines Windows and Ubuntu CI jobs.

[The recorded clean install](../evals/onboarding_verification.json) started
without models, recordings, credentials or installed packages. Setup downloaded
the assets, both services launched, local camera mapping worked, and the test
command passed all 306 backend tests, seven web tests and the production build.

## 2. Install the project

From the repository root:

```powershell
# Windows
py -3.13 run.py setup --demo
```

```bash
# Linux / macOS
python3.13 run.py setup --demo
```

The runner performs these steps, stopping at the first failure:

1. Checks Python, Node, npm, ffmpeg and ffprobe.
2. Creates `.venv`, or uses an existing Python 3.13 project environment.
3. Copies `.env.example` to `.env` **only if `.env` is absent**.
4. Installs the exact Python requirements and runs `pip check`.
5. Downloads and verifies `yolov8n.pt` and `yolov8n-pose.pt` in the repo root.
6. Runs `npm ci` in `web/` using the committed lockfile.
7. Loads both local models and runs CPU inference.
8. With `--demo`, downloads and verifies six public URFD recordings.

Only a successful setup writes `.venv/.aegis-ready.json`. Startup compares the
dependency manifests with that record, so pulling a dependency change prompts
you to rerun setup. Rerunning setup keeps `.env`, media and the database;
`npm ci` replaces the generated `web/node_modules/` installation. Stop Aegis
before rerunning setup.

## 3. Add your Meta key

Edit `.env` in a text editor:

```dotenv
META_API_KEY=your-key-here
```

Use a key from the [Meta Model API](https://dev.meta.ai/docs/overview) with access to:

- `sam-3.1`
- `muse-spark-1.3`
- `muse-voice-transcribe-1.0`

These are the hosted model identifiers used in `backend/app/meta.py`. The
runner does not provision API access or call paid models during installation.
Monitoring events and optional live probes make real provider calls.

Use plain `KEY=value` lines in `.env`; do not put quotes around values or add
comments on the same line. Existing environment variables take precedence over
the file. Restart the runner after editing it.

### Without a key

Local mapping, person/pose tracking, the deterministic rules and local tests
work without Meta. In the setup review, choose **Approve my configuration**.
Cloud reviews will show unavailable/failed states. If a local fall survives the
local rules while verification is unavailable, it still proceeds through the
resident check-in; it is not presented as a successful Muse verification.

## 4. Start and stop

```powershell
py -3.13 run.py
```

On Linux/macOS, use `python3.13 run.py`. For the commands below, `python` means
that same Python 3.13 interpreter. No environment activation is needed when
using `run.py`; it invokes `.venv` itself.

The runner starts:

- FastAPI at **http://127.0.0.1:8000**
- Next.js in development mode at **http://127.0.0.1:3000**

It waits for both services and opens `/setup`. **Keep the terminal open.**
Ctrl+C stops the processes it started. If a port is occupied, it exits with an
explanation rather than stopping the other program.

```bash
python run.py --no-browser
python run.py --api-port 8001 --web-port 3001
```

Use one command at a time. Alternate ports update the API proxy and allowed
browser origins for that run. The default API already runs its background
enrichment and escalation worker; no second worker is required.

## 5. Load the sample and follow an event

`setup --demo` puts six clips in `data/new-footage/`, which is the default lab
picker directory in `.env`. They total about 6.8 MB. Add them later with:

```bash
python run.py demo
```

These are **URFD camera-1 videos**, with depth and RGB displayed side-by-side,
downloaded from the University of Rzeszow. Their hashes match the six original
files used in [`evals/urfd_regression.json`](../evals/urfd_regression.json).
The downloader keeps those bytes intact; it does not crop or re-encode them.

1. Visit `/setup`, choose **Add lab footage**, add `fall-06.mp4`, and give it a room name.
2. Add a demo resident. Map the camera; this step runs locally.
3. Review the watch settings, approve the configuration, and complete the
   consent step for your demo household.
4. Finish setup. In the camera console, disable Loop and start monitoring.
5. Expand the camera. The event investigation appears when the local candidate
   fires. The model calls continue after the short recording has ended.
6. If the candidate proceeds, follow the resident check-in. The signed resident
   screen opens in another tab and offers “I'm okay” and “I need help.” A
   positive answer alone is not proof of physical recovery.
7. Inspect the two motion reads in the expanded camera or **A deeper look**.
   Open their inputs and responses. They are independent calls and can disagree.

The wall's no-answer deadline is 30 seconds **after check-in starts**, not
after the video starts. Cloud processing adds its own time. A new call may
produce a different result from the recorded run.

You can also upload a browser-playable recording or use a webcam. Uploaded
recording choices are stored in that browser. `localhost` and `127.0.0.1` have
separate browser storage, so keep using the runner's URL.

### Where the rest of the evaluation footage is

The sample downloader supplies the six URFD originals, not every historical
test input. CAVIAR and the 110-clip GMDCSA24 corpus were obtained separately.
See [`evals/corpus_manifest.json`](../evals/corpus_manifest.json), the source
references in [`evals/README.md`](../evals/README.md), and
[`scripts/build_footage.py`](../scripts/build_footage.py) for the expected source
names and reel processing. The app does not need those corpora to run.

For research, `python scripts/fetch_demo.py --directory data/footage/urfd`
downloads the same originals to the regression runner's expected location.
`build_footage.py` only processes source files you already have; it is not a
dataset downloader. The full regression also requires its listed reels and
external corpus. An incomplete corpus is not a reproduction of the full report.

## Installation checks and tests

```bash
python run.py check
python run.py test
```

`check` verifies setup, model hashes, Python dependency compatibility and real
local model loading. It reports whether a Meta key is present, never prints the
key, and makes no Meta request.

`test` runs the installed-environment backend audit and full suite, the
frontend inspection tests, and a production build including type validation.
It writes the backend report to `.tmp/verification.json`. Stop the app before
running it because Next's development server and build share `.next`.

Test counts in `evals/` describe their recorded source revisions. Adding tests
can change the count in your checkout. Live probes such as
`scripts/run_evals.py` additionally need the Meta key in their process
environment; they are not part of `run.py test`.

## Manual setup and production build

For development environments managed by hand, the equivalent steps are:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/fetch_weights.py
python scripts/fetch_demo.py
```

On Windows, create the environment with `py -3.13 -m venv .venv` and use
`.venv\Scripts\python.exe` for Python commands. Copy `.env.example` to `.env`
if needed, then add your key. Start `python scripts/serve.py` in one terminal.
In another:

```bash
cd web
npm ci
npm run dev
```

For a production web build, stop the development server, run `npm run build`,
then `npm run start`; keep the API running separately. The standard backend
target is `127.0.0.1:8000`. For a different target, set `AEGIS_API` in the web
process environment **when building**, since Next writes rewrites into the
build. `run.py` intentionally starts development mode for the local demo.

## Optional settings

| Setting | When you need it |
|---|---|
| `HOUSEHOLD_TZ` | Local schedules and timestamps; defaults to `Africa/Cairo`. |
| `FOOTAGE_ROOT` | Directory used by the lab picker. Default: `data/new-footage`. |
| `HOUSEHOLD_PIN`, `SESSION_SECRET` | PIN-based household access. Empty PIN keeps the localhost demo open. |
| `VAPID_*` | Web Push. Supply a matching public key and private PEM, then subscribe the browser. |
| `TWILIO_*`, `FAMILY_SMS_TO` | Optional SMS delivery. |
| `FAMILY_WEBHOOK_URL`, `FAMILY_WEBHOOK_SECRET` | Optional delivery to a household-owned webhook. |
| `CHECKIN_TTS` | Local spoken prompt; `off` leaves the browser voice fallback. |
| `AEGIS_RECHECK_MODEL` | Optional detector experiment. Leave unset for the default tested pipeline. |

In-app/console delivery works without phone or push configuration. None of
these settings turns a localhost URL into a remotely accessible family app;
remote use also needs appropriate hosting, HTTPS and access configuration.

The separate edge agent is configured in [`config/edge.yaml`](../config/edge.yaml).
With the project venv active, set `PYTHONPATH=edge` and run
`python -m aegis_edge --config config/edge.yaml`. It uses the configured USB,
RTSP or file source; microphone input is opt-in. Its immediate local-alert
behavior differs from the browser wall's pre-alert verification flow.

## Troubleshooting

| Symptom | What to check |
|---|---|
| “Use 64-bit CPython 3.13” / no matching Python wheel | Use the explicit `py -3.13` or `python3.13` command. An existing environment made with another version must be moved aside before setup. |
| ffmpeg / ffprobe not found | Add the installation's `bin` directory to `PATH`, then open a new terminal. |
| Port 3000 or 8000 is in use | Stop the old Aegis terminal or choose the alternate ports above. |
| Setup download fails | Check internet/proxy access to PyPI, the CPU PyTorch index, npm and GitHub Releases. Rerun setup after fixing connectivity. Sample clips come from `fenix.ur.edu.pl`. |
| Model checksum differs | The downloader preserves the existing file. Move that file aside and rerun `python scripts/fetch_weights.py`. Do not substitute a differently named model for the pinned one. |
| Meta 401/403 or unavailable model | Check your key and access to the three model identifiers above; restart after changing `.env`. Local installation success does not verify provider access. |
| Empty lab picker | Run `python run.py demo`; check `FOOTAGE_ROOT` if you changed it. You can always add a local recording instead. |
| Can't activate monitoring | Map every feed, approve the configuration and complete consent in Setup. |
| Recording disappeared | Return to the same browser/origin, or re-add the file. Browser storage does not travel with the Git checkout. |
| Next chunk/module errors after a build | Stop the app, remove only generated `web/.next`, then restart. Do not run `next dev` and `next build` against the same directory together. |
| Need the actual failure | Read `.tmp/api.log` and `.tmp/web.log`. API health: `http://127.0.0.1:8000/health`. |

[Back to the project →](../README.md)
