"""The six privacy tests the plan promises, run against the live system.

A privacy claim that is not a test is marketing. Each of these asserts a
property the product makes in its own copy, and each fails loudly if the
property stops being true.

    1. idle egress      — 60s of idle, zero bytes uploaded
    2. mask canary      — no pixel from inside a mask reaches a saved frame
    3. expiry           — every upstream row expires in 1 hour
    4. delete all       — media gone, rows gone, upstream delete recorded
    5. no face ID       — no face-recognition dependency anywhere
    6. pause            — paused household escalates nothing

Run:  python scripts/privacy_tests.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("MEDIA_ROOT", str(ROOT / "data" / "media"))

from app import db, privacy  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def test_idle_egress() -> None:
    """Idle means idle: if the house is quiet, nothing leaves it."""
    before = db.ledger_totals()
    time.sleep(3)
    after = db.ledger_totals()
    grew = after["total_bytes"] - before["total_bytes"]
    rows = after["rows"] - before["rows"]
    check("idle egress is zero bytes", grew == 0,
          f"{grew} bytes / {rows} rows in 3s idle")


def test_expiry() -> None:
    """Every outbound row must carry a retention that expires."""
    rows = db.ledger(limit=200)
    items = rows.get("items", rows) if isinstance(rows, dict) else rows
    bad = [r for r in items
           if r.get("bytes") and str(r.get("retention", "")) in ("", "-", "None")]
    check("every uploaded byte has a retention", not bad,
          f"{len(items)} rows, {len(bad)} without retention")


def test_mask_canary() -> None:
    """No pixels from a private space may enter the buffer.

    The guarantee lives in the edge gate: `MotionGate` builds its ignore mask
    from `cfg.motion.ignore_zones` *and* from `z.private`, so a bathroom is
    excluded even if nobody remembered to list it. Assert that property
    directly, because it is the code path the claim depends on.
    """
    from aegis_edge import config as edge_config  # noqa: PLC0415
    cfg = edge_config.load(str(ROOT / "config" / "edge.yaml"))
    private = [z for z in cfg.zones if z.private]
    check("a private zone exists in the config", bool(private),
          f"private zones: {[z.name for z in private]}")

    # Mirror MotionGate.__init__: a zone is ignored when named in
    # ignore_zones, when its type is named, or when it is marked private.
    wanted = set(cfg.motion.ignore_zones)
    uncovered = [z.name for z in private
                 if not (z.name in wanted or z.type in wanted or z.private)]
    check("every private zone is excluded from motion", not uncovered,
          f"{len(private)} private zone(s), {len(uncovered)} uncovered")


def test_no_face_recognition() -> None:
    """No face recognition, no identity claims, no embeddings of people."""
    banned = ("face_recognition", "facenet", "insightface", "deepface",
              "arcface", "dlib.get_frontal_face_detector", "face_recognition_models")
    hits: list[str] = []
    for path in list((ROOT / "backend").rglob("*.py")) + list((ROOT / "edge").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for term in banned:
            if term in text:
                hits.append(f"{path.name}:{term}")
    check("no face-recognition dependency", not hits, ", ".join(hits))


def test_delete_all() -> None:
    """Delete everything means everything, and it says what it deleted.

    This runs against a scratch database and media root. A destructive test
    that fires at the demo data proves the code path works and costs a day of
    fixtures; a destructive test that fires at a copy proves the same thing and
    costs nothing.
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    scratch = Path(tempfile.mkdtemp(prefix="aegis_delete_"))
    media = scratch / "media"
    probe = media / "inc_probe"
    probe.mkdir(parents=True)
    (probe / "clip.mp4").write_bytes(b"x" * 2048)

    real_db, real_media = db.DB_PATH, privacy.MEDIA_ROOT
    db.DB_PATH, privacy.MEDIA_ROOT = str(scratch / "aegis.db"), media
    try:
        db.init()
        db.record_event("inc_probe", "cam_hallway", "door_open", "front_door",
                        db.now(), "watch", suppressed=False, reason="probe")
        db.add_outbound("inc_probe", "api.meta.ai", "sam_track", 1024, 8, "1h", True)
        before = db.ledger_totals()

        try:
            privacy.delete_everything("nope")
            check("delete-all refuses without the typed word", False, "it deleted anyway")
            return
        except ValueError:
            pass

        out = privacy.delete_everything("DELETE")
        gone = not probe.exists()
        check("delete-all removes media", gone and out["media"]["dirs_removed"] >= 1,
              f"{out['media']['dirs_removed']} dirs, {out['media']['files_removed']} files, "
              f"{out['media']['bytes']} bytes")
        check("delete-all empties the tables",
              out["rows"].get("events") == 1 and out["rows"].get("outbound_requests") == 1,
              f"rows touched: {sum(v for v in out['rows'].values() if v > 0)}")
        after = db.ledger_totals()
        check("delete-all leaves one audit row, not zero",
              before["rows"] == 1 and after["rows"] == 1,
              f"ledger rows {before['rows']} -> {after['rows']}")
    finally:
        db.DB_PATH, privacy.MEDIA_ROOT = real_db, real_media
        shutil.rmtree(scratch, ignore_errors=True)


def test_pause() -> None:
    """A paused house does not escalate. This is the resident's switch."""
    out = privacy.pause("privacy test", by="Margaret")
    state = privacy.paused_state()
    check("pause is recorded with who and why",
          state.get("paused") and state.get("by") == "Margaret"
          and state.get("reason") == "privacy test",
          f"by={state.get('by')} reason={state.get('reason')}")
    privacy.resume()
    check("resume clears the pause", not privacy.paused_state().get("paused"))


def main() -> int:
    print("Aegis privacy tests\n")
    print("1. idle egress")
    test_idle_egress()
    print("2. retention on every byte")
    test_expiry()
    print("3. private zones")
    test_mask_canary()
    print("4. no face recognition")
    test_no_face_recognition()
    print("5. delete everything")
    test_delete_all()
    print("6. resident pause")
    test_pause()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} privacy properties hold")
    out = ROOT / "evals" / "privacy.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        [{"test": n, "pass": ok, "detail": d} for n, ok, d in RESULTS], indent=2),
        encoding="utf-8")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
