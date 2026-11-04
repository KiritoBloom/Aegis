# Submission reproducibility repair — 2026-11-04

## The four reported failures

A new CPython 3.13 environment installed from the original unpinned manifest
resolved HTTPX 0.28.1 and passed all 293 tests on this machine. Supplying
`HTTPS_PROXY=http://proxy:bad-port` reproduced exactly the four reported failures:
two escalation tests and two upload-signature tests. Their fake agents/clients
were installed only after real HTTP client construction parsed the host proxy.

Those four tests now substitute the client factory before construction and
include the malformed proxy as a regression condition. Production proxy support
is unchanged. This reproduces a concrete failure mechanism; it does not establish
which proxy settings existed on the judges' machine.

## Submitted environment

- Runtime, test and transitive dependencies are exactly pinned in
  `requirements.txt`; pytest and pytest-asyncio are included.
- Launchers and tests no longer put the ignored `.vendor` directory on the
  import path. The verification runner rejects dependencies imported from
  outside its virtual environment and flags installed packages without matching
  exact pins.
- The separate pinned CPU environment passed **293 tests, 0 failures, 0 skips**;
  `pip check` and the dependency-pin audit passed. Both local YOLO models loaded
  successfully there. See `verification.json` for the source fingerprint,
  installed versions and dependency origins.
- `fetch_weights.py` downloads the two required official, versioned assets and
  checks SHA-256 hashes. Both were independently downloaded and verified.
- Fresh-install Windows/Ubuntu backend and frontend jobs are defined in
  `.github/workflows/ci.yml`. Hosted CI execution is not claimed by this local
  report.
- An isolated frontend `npm ci`, typecheck, 7 inspection tests and production
  build passed. The submitted source was byte-compared with the build copy;
  `web_verification.json` records its hashes. Next.js 15.5.29 and React 19.1.10
  replace the patch versions flagged by the clean installation.

## Evidence and scope

The unused `sam_image()` method was removed. The three live probe runners now
record the source revision, modified-source flag, per-file hashes and package
versions; model failures remain in their output. Decision fixtures use three
calls per fixture, and check-in reading uses three calls per case. Their actual
outcomes are in `report.md` / `results.json`, `meta/sam_redaction.json`, and
`checkin_read.json`; these are not substitutes for the historical corpus gate.

The refreshed decision run passed **90/90 calls**, three runs on each of 30
fixtures through Muse and the policy validator. Median latency was 11,468 ms
and p95 was 15,125 ms; these packet checks exclude SAM and do not measure
standalone-model safety or population-level accuracy.

The refreshed SAM probe decoded **240/240 masks**, tracked 7 people, and applied
192 mask blurs covering 503,806 pixels with zero box fallbacks. The check-in
reading probe raised both word-list misses to help in all **6/6 calls**. These
are one video and two repeated synthetic cases, not population-level estimates.

Historical tracking, gate, corpus and privacy measurements are labelled as such.
The 8/57 floor-fall misses, 2/5 bed-fall pages and 2/48 ADL false pages have not
been resolved or remeasured by this repair. No real-home, real-user or real-phone
outcome is claimed.
