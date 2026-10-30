"""Two workers must not enrich the same incident.

This is a regression test for a defect found in the wild: an incident ended
`notified` with `attempts=4` and a phase-9 template decision written on top of
a phase-2 model decision that had already succeeded. Cause: the queue offered
the same row to every worker that asked, so two processes enriched it twice and
the loser's fail-open overwrote the winner.

Section 8.5 asks for a lease. This proves the lease is real: claim a row twice
from two connections and the second claim must come back empty.
Run: python tests/test_lease.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

# Point the module at a scratch database before importing it, so this test can
# never touch the demo's incidents.
SCRATCH = Path(tempfile.mkdtemp(prefix="aegis_lease_"))
os.environ["DATABASE_PATH"] = str(SCRATCH / "lease.db")

from app import db  # noqa: E402

# The environment variable only helps if this module imports db first; another
# test may have imported it already. Pin the module path explicitly so these
# tests can never claim the live demo's incidents (including check-in rows).
db.DB_PATH = str(SCRATCH / "lease.db")


def _seed(incident_id: str, with_clip: bool = True) -> None:
    db.upsert_incident({
        "incident_id": incident_id, "household_id": "hh_demo",
        "camera_id": "cam_hallway", "type": "door_open", "status": "detected",
        "started_at": db.now(), "local_time": db.now(), "zone_id": "front_door",
        "severity_hint": "watch", "track": {}, "sensors": {}, "privacy": {},
        "artifacts": {}, "evidence": [], "source": "test",
    })
    if with_clip:
        db.attach_clip(incident_id, "x/clip.mp4", None, None, 100)


def test_a_claimed_incident_is_not_offered_again():
    db.init()
    _seed("inc_lease_a")
    first = db.pending_for_processing(5)
    assert [i["id"] for i in first] == ["inc_lease_a"], first
    second = db.pending_for_processing(5)
    assert second == [], f"claimed row was offered twice: {second}"


def test_releasing_the_lease_makes_it_available_again():
    db.init()
    _seed("inc_lease_b")
    db.pending_for_processing(5)          # claims it
    assert db.pending_for_processing(5) == []
    db.release_lease("inc_lease_b")
    again = db.pending_for_processing(5)
    assert [i["id"] for i in again] == ["inc_lease_b"], again


def test_a_stale_lease_is_reclaimed_after_a_worker_dies():
    db.init()
    _seed("inc_lease_c")
    db.pending_for_processing(5)
    # A crashed worker never released. With a zero-second lease the claim is
    # already stale, which is how the queue recovers without human help.
    # (Earlier tests in this file leave stale claims of their own, so assert
    # this incident is offered again rather than that it is offered alone.)
    recovered = [i["id"] for i in db.pending_for_processing(5, lease_s=0.0)]
    assert "inc_lease_c" in recovered, recovered


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                fails += 1
                print(f"  FAIL {name}: {exc}")
    print(f"\n{fails} failure(s)")
    sys.exit(1 if fails else 0)
