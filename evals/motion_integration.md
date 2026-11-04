# Live image-versus-hex demo integration

Validated on 2026-11-04. Both the expanded camera investigation and **A deeper
look** display independent, persisted Muse motion reviews. Each view exposes
the exact input, instructions/schema, returned action, model confidence,
latency and cited observations. Completed reads are reused; retry starts only
unfinished reads.

## Observed live result

Uploaded `data/footage/urfd/fall-06.mp4`, mapped the camera, disabled Loop, and
started monitoring. The short recording raised a local candidate before ending.
Incident `inc_wall_bc484726ea13` produced these real responses:

| Input | Returned action | Model confidence | Model-call latency |
|---|---|---|---|
| Protected chronological contact sheet | `fall` | 68% | 18,083 ms |
| Text-only hexadecimal occupancy | `getting_up` | 82% | 19,467 ms |

Both views visibly reported the disagreement. These demo results do not update
the incident decision or release family notifications. The resident check-in
and outcome remain visible above the comparison in the expanded camera view.

Both inputs contain 24 observations of the same SAM target. The image request
has one image attachment; the hex request has zero image/video attachments
and zero tools. Its 32×18 binary occupancy takes 1,728 bytes; the saved JSON
packet is 6,277 bytes. Instructions, schema and transport are additional.

Read-only browser checks downloaded the exposed artifacts and verified their
SHA-256 hashes against the persisted requests. The decoded hex JSON also
matched the persisted request packet exactly:

- `motion_image.jpg`, 174,131 bytes:
  `1aa24a70d3e0e8544ea493d3123b8d7d1af5678542f20afdeae9e29ca86872cf`
- `motion_hex.txt`, 6,277 bytes:
  `6738c485435973fb89bb4bf36f328ed92f656825bb3489f8e25fadc6c9efc3c6`

Screenshots are local, under `.tmp/muse-comparison-enlarged.png`,
`.tmp/muse-comparison-deeper.png`, `.tmp/muse-comparison-deeper-inputs.png`,
and `.tmp/muse-comparison-mobile.png`. They are not committed footage.

## Automated checks

- `python -m pytest -q`: **301 passed**, three dependency deprecation warnings.
- `node --test tests/inspection.test.cjs`: **7 passed**.
- Production build, including type validation: **passed** with Next.js
  **15.5.29** and React **19.1.10**. Current source was byte-verified in the
  lockfile-matched isolated installation. Commands, output, versions and source
  hashes are recorded in [`motion_web_verification.json`](motion_web_verification.json).
- Motion tests cover target-only packing, missing-mask zeros, timestamps,
  atomic artifact writes, concurrent independent reviews, one-sided failure,
  unchanged incident/notification state, and task deduplication.
- Tracker tests cover canvas-width-invariant geometry, confidently inverted
  overhead bodies versus forward bends, and continuity of a corroborated lying
  track while it turns.

The implementation is in `backend/app/motion_review.py`,
`backend/app/api.py`, and `web/components/muse-comparison.tsx`.
This single live disagreement demonstrates the integration, not model accuracy.

The accompanying local detector change is measured separately in
[`urfd_regression.md`](urfd_regression.md), including the additional ADL candidates.
