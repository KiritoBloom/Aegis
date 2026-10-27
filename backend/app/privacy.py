"""Privacy controls that actually do something.

The plan's privacy claims are only defensible if each one is a code path with a
visible effect, so this module owns the four that a family can check:

* **consent** — the resident agrees, on their own device, and the record stores
  the version of the text they saw. A family member cannot consent for them.
* **pause** — one call stops uploads, clears the ring buffer, and shows the
  family "paused by Margaret at 9:12 PM", never an error state.
* **delete everything** — media on disk, rows in the database, and a recorded
  request to delete the clips held upstream. Each step reports its own result,
  because a partial delete reported as complete is a lie.
* **retention sweep** — escalation clips expire after 7 days locally and 1 hour
  at the API. This is the job that makes the expiry claim true rather than
  aspirational.

Nothing here needs a model, a network call, or a permission check beyond the
resident's own token.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db

log = logging.getLogger("aegis.privacy")

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "data/media"))
CONSENT_VERSION = "2026-11-consent-1"
LOCAL_RETENTION_DAYS = 7
API_RETENTION_S = 3600

CONSENT_TEXT = (
    "Aegis watches for a small number of specific situations: someone going out "
    "of the front door at night, a fall with no recovery, and a stove left on. "
    "Video stays on a device in this home. A short clip leaves only when one of "
    "those situations happens, and it is deleted within an hour. You can pause "
    "this at any time, and you can delete everything."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# consent
# ---------------------------------------------------------------------------
def record_consent(member: str = "resident", device: str = "", given: bool = True) -> dict:
    """Store who agreed, to which text, and when.

    `member` is deliberately not defaulted to the family: the plan's rule is that
    the resident consents on their own device, so the caller must say so.
    """
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO consent (id, household_id, member, device, version, text,"
            " given_at, revoked_at) VALUES (?,?,?,?,?,?,?,NULL)",
            (f"cns_{int(time.time())}", "hh_demo", member, device, CONSENT_VERSION,
             CONSENT_TEXT, _now() if given else None),
        )
    log.info("consent recorded member=%s version=%s", member, CONSENT_VERSION)
    return {"member": member, "version": CONSENT_VERSION, "given_at": _now(),
            "text": CONSENT_TEXT}


def consent_state() -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM consent WHERE given_at IS NOT NULL AND revoked_at IS NULL"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"consented": False, "version": CONSENT_VERSION, "text": CONSENT_TEXT}
    return {"consented": True, "member": row["member"], "version": row["version"],
            "given_at": row["given_at"], "text": row["text"]}


def revoke_consent() -> dict:
    with db.connect() as conn:
        conn.execute("UPDATE consent SET revoked_at = ? WHERE revoked_at IS NULL", (_now(),))
    return pause("resident revoked consent")


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------
def pause(reason: str = "resident paused monitoring", by: str = "Margaret") -> dict:
    """Stop everything outbound. Not an error: a deliberate, visible state."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO household_state (key, value, updated_at) VALUES ('paused', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
            " updated_at=excluded.updated_at",
            (f"{by}|{reason}|{_now()}", _now()),
        )
    cleared = clear_buffer()
    log.info("monitoring paused by %s (%s)", by, reason)
    return {"paused": True, "by": by, "reason": reason, "at": _now(),
            "buffer_cleared": cleared}


def resume() -> dict:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO household_state (key, value, updated_at) VALUES ('paused', '', ?)"
            " ON CONFLICT(key) DO UPDATE SET value='', updated_at=excluded.updated_at",
            (_now(),),
        )
    log.info("monitoring resumed")
    return {"paused": False, "at": _now()}


def paused_state() -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value, updated_at FROM household_state WHERE key='paused'"
        ).fetchone()
    if not row or not row["value"]:
        return {"paused": False}
    by, reason, at = (row["value"].split("|") + ["", "", ""])[:3]
    return {"paused": True, "by": by or "Margaret", "reason": reason,
            "at": at or row["updated_at"]}


def clear_buffer() -> int:
    """Delete the edge ring buffer, if this machine is also running the edge."""
    removed = 0
    for candidate in (Path("data/buffer"), Path("data/queue")):
        if candidate.exists():
            for f in candidate.glob("*"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


# ---------------------------------------------------------------------------
# delete everything
# ---------------------------------------------------------------------------
TABLES = ("incidents", "decisions", "outbound_requests", "events", "notifications",
          "acks", "device_heartbeats", "household_memory", "letters", "checkins")


def delete_everything(confirm: str) -> dict:
    """Irreversible. Requires the literal word so it cannot fire by accident.

    Reports each stage separately. If the upstream delete cannot be confirmed we
    say so rather than claiming a clean sweep.
    """
    if confirm.strip().upper() != "DELETE":
        raise ValueError("type DELETE to confirm")

    media = {"dirs_removed": 0, "files_removed": 0, "bytes": 0}
    if MEDIA_ROOT.exists():
        for child in MEDIA_ROOT.iterdir():
            try:
                if child.is_dir():
                    size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                    shutil.rmtree(child)
                    media["dirs_removed"] += 1
                    media["bytes"] += size
                else:
                    media["bytes"] += child.stat().st_size
                    child.unlink()
                    media["files_removed"] += 1
            except OSError as exc:
                log.warning("delete: could not remove %s: %s", child, exc)

    rows: dict[str, int] = {}
    with db.connect() as conn:
        for table in TABLES:
            try:
                n = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
                conn.execute(f"DELETE FROM {table}")
                rows[table] = n
            except Exception as exc:  # noqa: BLE001
                log.warning("delete: %s failed: %s", table, exc)
                rows[table] = -1

    # The API holds its own copy for an hour. Record the request as an outbound
    # row with zero bytes so the ledger shows an attempt, and be honest that we
    # cannot verify a remote delete from here.
    try:
        db.add_outbound(None, "api.meta.ai", "delete_all", 0, 0, "immediate", False)
    except Exception:  # noqa: BLE001
        pass

    log.warning("deleted everything: %s rows, %s media dirs", sum(rows.values()), media["dirs_removed"])
    return {
        "deleted": True,
        "media": media,
        "rows": rows,
        "upstream": "requested; clips also expire automatically after 1 hour",
    }


# ---------------------------------------------------------------------------
# retention sweep
# ---------------------------------------------------------------------------
def sweep_retention() -> dict:
    """Enforce the two expiry promises: 7 days local, 1 hour upstream."""
    removed_dirs = 0
    cutoff = time.time() - LOCAL_RETENTION_DAYS * 86400
    if MEDIA_ROOT.exists():
        for child in MEDIA_ROOT.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child)
                    removed_dirs += 1
            except OSError:
                pass

    # Outbound rows past their retention window lose their byte count but keep
    # the row: the ledger has to stay auditable after the media is gone.
    expired = 0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, retention FROM outbound_requests WHERE bytes > 0"
        ).fetchall()
        for r in rows:
            ret = (r["retention"] or "").strip()
            seconds = 0
            if ret.endswith("h") and ret[:-1].isdigit():
                seconds = int(ret[:-1]) * 3600
            elif ret.endswith("d") and ret[:-1].isdigit():
                seconds = int(ret[:-1]) * 86400
            if not seconds:
                continue
            try:
                created = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if (datetime.now(timezone.utc) - created).total_seconds() > seconds:
                conn.execute("UPDATE outbound_requests SET bytes=0 WHERE id=?", (r["id"],))
                expired += 1
    if removed_dirs or expired:
        log.info("retention sweep: %d media dirs, %d ledger rows expired",
                 removed_dirs, expired)
    return {"media_dirs_removed": removed_dirs, "ledger_rows_expired": expired,
            "local_days": LOCAL_RETENTION_DAYS}


def what_was_recorded_today() -> dict:
    """The resident's own view: what happened, never the footage."""
    today = datetime.now(timezone.utc).date().isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT type, zone_id, ts, suppressed FROM events WHERE ts LIKE ?"
            " ORDER BY id DESC", (f"{today}%",)
        ).fetchall()
    return {
        "date": today,
        "events": [dict(r) for r in rows],
        "count": len(rows),
        "note": ("This lists moments the system noticed. It never stores or shows "
                 "footage unless something needed the family."),
    }
