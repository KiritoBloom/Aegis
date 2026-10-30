"""SQLite storage. One file, WAL, no ORM.

Metadata is kept for 90 days; media is not kept here at all. The ledger table is
append-only and is the source of truth for "what left your home".
"""
from __future__ import annotations

import calendar
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = os.environ.get("DATABASE_PATH", "data/aegis.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
  id TEXT PRIMARY KEY, name TEXT, timezone TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS cameras (
  id TEXT PRIMARY KEY, household_id TEXT, name TEXT, location TEXT,
  status TEXT, last_heartbeat_at TEXT, detector TEXT, config_json TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, household_id TEXT, camera_id TEXT, type TEXT,
  status TEXT, severity TEXT, started_at TEXT, local_time TEXT, zone_id TEXT,
  features_json TEXT, sensors_json TEXT, privacy_json TEXT, evidence_json TEXT,
  clip_path TEXT, still_path TEXT, thumb_path TEXT, source TEXT,
  severity_hint TEXT, created_at TEXT, sam_json TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, incident_id TEXT, phase INTEGER, severity TEXT,
  policy_floor TEXT, floor_reasons_json TEXT, title TEXT, body TEXT,
  evidence_json TEXT, actions_json TEXT, confidence REAL, notify_now INTEGER,
  follow_up_s INTEGER, rationale TEXT, model TEXT, prompt_version TEXT,
  violations_json TEXT, latency_ms INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS outbound_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT, destination TEXT,
  purpose TEXT, bytes INTEGER, frames INTEGER, retention TEXT,
  contains_video INTEGER, contains_audio INTEGER DEFAULT 0,
  created_at TEXT, latency_ms INTEGER, model TEXT
);
CREATE TABLE IF NOT EXISTS device_heartbeats (
  id INTEGER PRIMARY KEY AUTOINCREMENT, camera_id TEXT, ts TEXT, fps REAL,
  frames INTEGER, queue_depth INTEGER, camera_state TEXT, detector TEXT
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
  id TEXT PRIMARY KEY, endpoint TEXT UNIQUE, p256dh TEXT, auth TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS acks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT, action TEXT,
  created_at TEXT
);
-- Every event the edge saw, including the ones the policy deliberately did not
-- escalate. The restraint counter is only honest if the denominator is here.
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT, camera_id TEXT,
  type TEXT, zone_id TEXT, ts TEXT, severity TEXT, suppressed INTEGER DEFAULT 0,
  reason TEXT, source TEXT, created_at TEXT
);
-- Written BEFORE the network call, so a failed send is a recorded fact rather
-- than a missing row.
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY, incident_id TEXT, channel TEXT, phase INTEGER,
  status TEXT, severity TEXT, title TEXT, body TEXT, external_id TEXT,
  error TEXT, attempts INTEGER DEFAULT 0, sent_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS household_memory (
  id TEXT PRIMARY KEY, household_id TEXT, fact TEXT, confidence REAL,
  source TEXT, created_at TEXT, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS checkins (
  id TEXT PRIMARY KEY, incident_id TEXT, transcript TEXT, outcome TEXT,
  model TEXT, created_at TEXT
);
-- Who agreed, to which version of the text, and when. The resident consents on
-- their own device; a family member cannot do it for them.
CREATE TABLE IF NOT EXISTS consent (
  id TEXT PRIMARY KEY, household_id TEXT, member TEXT, device TEXT,
  version TEXT, text TEXT, given_at TEXT, revoked_at TEXT
);
-- Single-row settings: the pause flag lives here so the UI can show
-- "paused by Margaret at 9:12 PM" instead of an error.
CREATE TABLE IF NOT EXISTS household_state (
  key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_notif_inc ON notifications(incident_id, phase);
CREATE INDEX IF NOT EXISTS idx_inc_started ON incidents(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_out_created ON outbound_requests(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hb_cam ON device_heartbeats(camera_id, ts DESC);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the first deploy. CREATE TABLE IF NOT EXISTS will not add
# them to an existing file, and silently losing a column mid-demo is worse than
# a two-line migration.
MIGRATIONS = [
    ("incidents", "sam_json", "TEXT"),
    ("incidents", "agent_json", "TEXT"),
    ("incidents", "attempts", "INTEGER DEFAULT 0"),
    ("incidents", "notify_phase", "INTEGER DEFAULT 0"),
    ("incidents", "escalation_due_at", "REAL"),
    ("incidents", "acked_at", "TEXT"),
    ("incidents", "deferred_until", "REAL"),
    ("outbound_requests", "latency_ms", "INTEGER"),
    ("outbound_requests", "model", "TEXT"),
    ("outbound_requests", "contains_audio", "INTEGER DEFAULT 0"),
    ("incidents", "enrich_lease", "REAL"),
    ("decisions", "packet_json", "TEXT"),
    # The spoken check-in is a state machine, not a button. These columns make
    # the deadline survive an API restart and let the dashboard show exactly
    # what was said, when, and whether anyone answered.
    ("incidents", "checkin_status", "TEXT"),
    ("incidents", "checkin_due_at", "REAL"),
    ("incidents", "checkin_prompt", "TEXT"),
    ("incidents", "checkin_started_at", "REAL"),
    ("incidents", "checkin_answered_at", "REAL"),
    ("incidents", "checkin_timeout_s", "REAL"),
    ("checkins", "read_json", "TEXT"),
    ("incidents", "context_json", "TEXT"),
    ("device_heartbeats", "scene_json", "TEXT"),
    ("device_heartbeats", "scene_at", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute(
            "INSERT OR IGNORE INTO households (id, name, timezone, created_at) VALUES (?,?,?,?)",
            ("hh_demo", "Cairo apartment", "Africa/Cairo", now()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO cameras (id, household_id, name, location, status, detector) "
            "VALUES (?,?,?,?,?,?)",
            ("cam_hallway", "hh_demo", "Hallway", "hallway", "online", "yolo"),
        )
        conn.commit()


def upsert_incident(payload: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO incidents
               (id, household_id, camera_id, type, status, severity, started_at, local_time,
                zone_id, features_json, sensors_json, privacy_json, evidence_json, source,
                severity_hint, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO NOTHING""",
            (
                payload["incident_id"], payload.get("household_id"), payload.get("camera_id"),
                payload.get("type"), payload.get("status", "detected"),
                payload.get("severity_hint") or "info", payload.get("started_at"),
                payload.get("local_time"), payload.get("zone_id"),
                json.dumps(payload.get("track") or {}),
                json.dumps(payload.get("sensors") or {}),
                json.dumps(payload.get("privacy") or {}),
                json.dumps(payload.get("evidence") or []),
                payload.get("source", "live"),
                payload.get("severity_hint"), now(),
            ),
        )
        conn.commit()


def attach_clip(incident_id: str, clip: str, still: str | None, thumb: str | None,
                size_bytes: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE incidents SET clip_path=?, still_path=?, thumb_path=? WHERE id=?",
            (clip, still, thumb, incident_id),
        )
        conn.execute(
            "INSERT INTO outbound_requests (incident_id, destination, purpose, bytes, frames,"
            " retention, contains_video, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (incident_id, "local-edge", "escalation_clip", size_bytes, 0, "7d", 1, now()),
        )
        conn.commit()


def attach_sam(incident_id: str, features: dict) -> None:
    """Persist what SAM saw, so the UI can show it without re-reading the clip."""
    with connect() as conn:
        conn.execute("UPDATE incidents SET sam_json=? WHERE id=?",
                     (json.dumps(features), incident_id))
        conn.commit()


def attach_agent(incident_id: str, trace: list[dict]) -> None:
    """Persist the evidence-agent steps for the human-readable audit trail."""
    with connect() as conn:
        conn.execute("UPDATE incidents SET agent_json=? WHERE id=?",
                     (json.dumps(trace), incident_id))
        conn.commit()


def attach_context_read(incident_id: str, read: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE incidents SET context_json=? WHERE id=?",
                     (json.dumps(read), incident_id))
        conn.commit()


def update_incident_sensors(incident_id: str, patch: dict) -> None:
    """Merge new perception into an open incident."""
    with connect() as conn:
        row = conn.execute("SELECT sensors_json FROM incidents WHERE id=?",
                           (incident_id,)).fetchone()
        if row is None:
            return
        sensors = json.loads(row[0] or "{}")
        sensors.update(patch)
        conn.execute("UPDATE incidents SET sensors_json=? WHERE id=?",
                     (json.dumps(sensors), incident_id))
        conn.commit()


def set_status(incident_id: str, status: str, severity: str | None = None) -> None:
    with connect() as conn:
        if severity:
            # Only an explicit resolution may lower severity. A late model write
            # or a status refresh must not undo an escalation that already
            # reached a phone.
            if status != "resolved":
                order = {"info": 0, "watch": 1, "urgent": 2, "emergency": 3}
                row = conn.execute("SELECT severity FROM incidents WHERE id=?",
                                   (incident_id,)).fetchone()
                current = row["severity"] if row else "info"
                if order.get(severity, 0) < order.get(current, 0):
                    severity = current
            conn.execute("UPDATE incidents SET status=?, severity=? WHERE id=?",
                         (status, severity, incident_id))
        else:
            conn.execute("UPDATE incidents SET status=? WHERE id=?", (status, incident_id))
        conn.commit()


def raise_incident_severity(incident_id: str, severity: str) -> None:
    """Raise severity without ever lowering it."""
    order = {"info": 0, "watch": 1, "urgent": 2, "emergency": 3}
    with connect() as conn:
        row = conn.execute("SELECT severity FROM incidents WHERE id=?", (incident_id,)).fetchone()
        current = row["severity"] if row else "info"
        chosen = severity if order.get(severity, 0) >= order.get(current, 0) else current
        conn.execute("UPDATE incidents SET severity=? WHERE id=?", (chosen, incident_id))
        conn.commit()


def _decode_incident(inc: dict) -> dict:
    """Decode stored JSON columns into the names the policy rules read."""
    # Column names and payload names differ for track (stored as features_json).
    for key, column, empty in (("track", "features_json", {}),
                               ("sensors", "sensors_json", {}),
                               ("privacy", "privacy_json", {}),
                               ("evidence", "evidence_json", [])):
        try:
            inc[key] = json.loads(inc.pop(column, None) or "null")
        except (json.JSONDecodeError, TypeError):
            inc[key] = empty
        if inc[key] is None:
            inc[key] = empty
    try:
        inc["sam"] = json.loads(inc.pop("sam_json", None) or "null")
    except (json.JSONDecodeError, TypeError):
        inc["sam"] = None
    try:
        inc["agent"] = json.loads(inc.pop("agent_json", None) or "null")
    except (json.JSONDecodeError, TypeError):
        inc["agent"] = None
    try:
        inc["context_read"] = json.loads(inc.pop("context_json", None) or "null")
    except (json.JSONDecodeError, TypeError):
        inc["context_read"] = None
    return inc


def get_incident(incident_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            return None
        inc = _decode_incident(dict(row))
        dec = conn.execute(
            "SELECT * FROM decisions WHERE incident_id=? ORDER BY phase DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
        inc["decision"] = _decode_decision(dec) if dec else None
        inc["ledger"] = [dict(r) for r in conn.execute(
            "SELECT * FROM outbound_requests WHERE incident_id=? ORDER BY id", (incident_id,)
        )]
        inc["checkin"] = latest_checkin(incident_id)
        return inc


def list_incidents(limit: int = 50, status: str | None = None) -> list[dict]:
    q = "SELECT * FROM incidents"
    args: list[Any] = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            inc = dict(r)
            dec = conn.execute(
                "SELECT * FROM decisions "
                "WHERE incident_id=? ORDER BY phase DESC LIMIT 1", (inc["id"],)
            ).fetchone()
            if dec:
                order = {"info": 0, "watch": 1, "urgent": 2, "emergency": 3}
                inc["decision"] = _decode_decision(dec)
                inc["title"] = dec["title"]
                if order.get(dec["severity"], 0) >= order.get(inc.get("severity"), 0):
                    inc["severity"] = dec["severity"]
                inc["notify_now"] = bool(dec["notify_now"])
                inc["policy_floor"] = dec["policy_floor"]
            else:
                inc["decision"] = None
            try:
                inc["sensors"] = json.loads(inc.pop("sensors_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                inc["sensors"] = {}
            try:
                inc["agent"] = json.loads(inc.pop("agent_json") or "null")
            except (json.JSONDecodeError, TypeError):
                inc["agent"] = None
            try:
                inc["evidence"] = json.loads(inc.pop("evidence_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                inc["evidence"] = []
            inc.pop("features_json", None)
            inc.pop("privacy_json", None)
            inc.pop("sam_json", None)
            out.append(inc)
    return out


def _decode_decision(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("evidence_json", "actions_json", "floor_reasons_json", "violations_json"):
        try:
            d[k.replace("_json", "")] = json.loads(d.pop(k) or "[]")
        except json.JSONDecodeError:
            d[k.replace("_json", "")] = []
    return d


def add_decision(incident_id: str, phase: int, floor: str, floor_reasons: list[str],
                 decision, model: str, prompt_version: str, violations: list[str],
                 latency_ms: int) -> str:
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO decisions
               (id, incident_id, phase, severity, policy_floor, floor_reasons_json, title, body,
                evidence_json, actions_json, confidence, notify_now, follow_up_s, rationale,
                model, prompt_version, violations_json, latency_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"dec_{incident_id}_{phase}", incident_id, phase, decision.severity, floor,
                json.dumps(floor_reasons), decision.title, decision.body,
                json.dumps(decision.evidence), json.dumps(decision.actions),
                decision.confidence, int(decision.notify_now), decision.follow_up_s,
                decision.rationale, model, prompt_version, json.dumps(violations),
                latency_ms, now(),
            ),
        )
        # A late phase-2 enrichment must never quietly lower a phase-3
        # check-in escalation that has already reached a phone.
        order = {"info": 0, "watch": 1, "urgent": 2, "emergency": 3}
        row = conn.execute("SELECT severity FROM incidents WHERE id=?", (incident_id,)).fetchone()
        current = row["severity"] if row else "info"
        chosen = decision.severity if order.get(decision.severity, 0) >= order.get(current, 0) else current
        conn.execute("UPDATE incidents SET severity=? WHERE id=?", (chosen, incident_id))
        conn.commit()
        return chosen


def add_outbound(incident_id: str | None, destination: str, purpose: str, nbytes: int,
                 frames: int = 0, retention: str = "-", contains_video: bool = False,
                 latency_ms: int = 0, model: str | None = None,
                 contains_audio: bool = False) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO outbound_requests (incident_id, destination, purpose, bytes, frames,"
            " retention, contains_video, contains_audio, created_at, latency_ms, model)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, destination, purpose, nbytes, frames, retention,
             int(contains_video), int(contains_audio), now(), latency_ms, model),
        )
        conn.commit()


def ledger(limit: int = 200) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM outbound_requests ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def ledger_totals() -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(bytes),0) AS total, COUNT(*) AS rows FROM outbound_requests"
        ).fetchone()
        idle = conn.execute(
            "SELECT COALESCE(SUM(bytes),0) AS b FROM outbound_requests "
            "WHERE purpose='idle_probe'"
        ).fetchone()
        # "What left your home" is only honest if it can answer the question
        # the resident actually asks: was I seen, and was I heard?
        media = conn.execute(
            "SELECT COALESCE(SUM(bytes),0) AS video FROM outbound_requests"
            " WHERE contains_video=1"
        ).fetchone()
        audio = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(bytes),0) AS b FROM outbound_requests"
            " WHERE contains_audio=1"
        ).fetchone()
    return {"total_bytes": row["total"], "rows": row["rows"], "idle_bytes": idle["b"],
            "video_bytes": media["video"],
            "audio_rows": audio["n"], "audio_bytes": audio["b"]}


def add_heartbeat(payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO device_heartbeats (camera_id, ts, fps, frames, queue_depth,"
            " camera_state, detector, scene_json, scene_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (payload.get("camera_id"), payload.get("ts_local"), payload.get("fps"),
             payload.get("frames"), payload.get("queue_depth"),
             payload.get("camera_state"), payload.get("detector"),
             json.dumps(payload.get("objects") or []), payload.get("objects_at")),
        )
        conn.execute("UPDATE cameras SET last_heartbeat_at=?, status=?, detector=? WHERE id=?",
                     (now(), payload.get("camera_state", "online"),
                      payload.get("detector"), payload.get("camera_id")))
        conn.commit()


def last_heartbeat() -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM device_heartbeats ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    hb = dict(row)
    try:
        hb["objects"] = json.loads(hb.pop("scene_json", None) or "[]")
    except (json.JSONDecodeError, TypeError):
        hb["objects"] = []
    hb["objects_at"] = hb.get("scene_at")
    return hb


def add_ack(incident_id: str, action: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO acks (incident_id, action, created_at) VALUES (?,?,?)",
                     (incident_id, action, now()))
        status = "resolved" if action in ("im_checking", "false_alarm", "send_help") else "acknowledged"
        conn.execute("UPDATE incidents SET status=? WHERE id=?", (status, incident_id))
        conn.commit()


def add_subscription(sub_id: str, endpoint: str, p256dh: str, auth: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO push_subscriptions (id, endpoint, p256dh, auth, created_at)"
            " VALUES (?,?,?,?,?)",
            (sub_id, endpoint, p256dh, auth, now()),
        )
        conn.commit()


def subscriptions() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM push_subscriptions")]


def drop_subscription(sub_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE id=?", (sub_id,))
        conn.commit()


def record_failure(incident_id: str, reason: str) -> None:
    """Record a failed delivery and release it for retry."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO outbound_requests (incident_id, destination, purpose, bytes, frames,"
            " retention, contains_video, created_at, model) VALUES (?,?,?,?,?,?,?,?,?)",
            (incident_id, "internal", "enrich_failed", 0, 0, "-", 0, now(), reason[:80]),
        )
        conn.commit()


def bump_attempt(incident_id: str) -> int:
    with connect() as conn:
        conn.execute("UPDATE incidents SET attempts=COALESCE(attempts,0)+1 WHERE id=?",
                     (incident_id,))
        row = conn.execute("SELECT attempts FROM incidents WHERE id=?",
                           (incident_id,)).fetchone()
        conn.commit()
    return int(row["attempts"]) if row else 0


def pending_for_processing(limit: int = 10, grace_s: float = 8.0,
                           lease_s: float = 180.0) -> list[dict]:
    """Claim enrichable incidents with a lease so two workers cannot process one row."""
    cutoff = time.time() - grace_s
    lease_cutoff = time.time() - lease_s
    claimed: list[dict] = []
    with connect() as conn:
        # BEGIN IMMEDIATE takes the write lock before the read, so two processes
        # cannot both decide a row is free and both claim it.
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM incidents WHERE status IN ('detected','enriching') "
            " AND (enrich_lease IS NULL OR enrich_lease < ?) "
            "ORDER BY created_at LIMIT ?",
            (lease_cutoff, limit * 4),
        ).fetchall()
        for r in rows:
            if len(claimed) >= limit:
                break
            inc = _decode_incident(dict(r))
            if not inc.get("clip_path"):
                try:
                    # created_at is UTC; time.mktime would read it as local time
                    # and a +3 offset would silently cancel the grace window.
                    created = calendar.timegm(
                        time.strptime(inc["created_at"], "%Y-%m-%dT%H:%M:%SZ"))
                except Exception:  # noqa: BLE001
                    created = 0.0
                if created > cutoff:
                    continue          # still waiting for its clip
            got = conn.execute(
                "UPDATE incidents SET enrich_lease=? WHERE id=? "
                " AND (enrich_lease IS NULL OR enrich_lease < ?)",
                (time.time(), inc["id"], lease_cutoff),
            )
            if got.rowcount:
                inc["enrich_lease"] = time.time()
                claimed.append(inc)
        conn.commit()
    return claimed


def release_lease(incident_id: str) -> None:
    """Give a finished incident back, so a crash-free run never looks stuck."""
    with connect() as conn:
        conn.execute("UPDATE incidents SET enrich_lease=NULL WHERE id=?",
                     (incident_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# events: the denominator of the restraint counter
# ---------------------------------------------------------------------------
def record_event(incident_id: str | None, camera_id: str | None, etype: str,
                 zone_id: str | None, ts: str, severity: str, *,
                 suppressed: bool = False, reason: str = "",
                 source: str = "live") -> None:
    """Log an occurrence whether or not it became an alert.

    `suppressed` is the product claim, so it is a column, not an inference from
    the absence of a row.
    """
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (incident_id, camera_id, type, zone_id, ts, severity,"
            " suppressed, reason, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (incident_id, camera_id, etype, zone_id, ts, severity,
             int(suppressed), reason[:200], source, now()),
        )
        conn.commit()


def event_counts(days: int = 7) -> dict:
    """Real COUNT(*) at render time — never a hardcoded figure."""
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        since = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE created_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchone()["c"]
        sent = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE suppressed=0 AND severity IN "
            "('urgent','emergency')"
        ).fetchone()["c"]
        by_zone = conn.execute(
            "SELECT COALESCE(zone_id,'unknown') AS name, COUNT(*) AS count FROM events "
            "WHERE created_at >= datetime('now', ?) GROUP BY 1 ORDER BY count DESC",
            (f"-{days} days",),
        ).fetchall()
        by_type = conn.execute(
            "SELECT type AS name, COUNT(*) AS count FROM events "
            "WHERE created_at >= datetime('now', ?) "
            "GROUP BY 1 ORDER BY count DESC", (f"-{days} days",),
        ).fetchall()
        kept = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE suppressed=1"
        ).fetchone()["c"]
    return {
        "seen": total,
        "seen_window": since,
        "alerts": sent,
        "stayed_home": kept,
        "window_days": days,
        "by_zone": [dict(r) for r in by_zone],
        "by_type": [dict(r) for r in by_type],
    }


def recent_events(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def suppressed_sample(limit: int = 20) -> list[dict]:
    """The non-alerts. The demo is the product, so these need to be visible."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE suppressed=1 ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def previous_event(incident_id: str) -> dict | None:
    """The most recent event before this one, with how long ago it happened.

    Dedupe is a decision about *this* event relative to the last one, so the
    age has to be computed against the incident's own clock, not wall time —
    a replayed incident from this morning must dedupe as it would have then.
    """
    with connect() as conn:
        cur = conn.execute(
            "SELECT id, ts FROM events WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if not cur:
            return None
        row = conn.execute(
            "SELECT e.*, (SELECT a.action FROM acks a JOIN events e2"
            "  ON a.incident_id = e2.incident_id"
            "  WHERE e2.id = e.id ORDER BY a.id DESC LIMIT 1) AS outcome"
            " FROM events e WHERE e.id < ? ORDER BY e.id DESC LIMIT 1",
            (cur["id"],),
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        then = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
        nown = datetime.fromisoformat(str(cur["ts"]).replace("Z", "+00:00"))
        out["age_s"] = abs((nown - then).total_seconds())
    except (ValueError, TypeError):
        out["age_s"] = 0.0
    return out


# ---------------------------------------------------------------------------
# notifications: a row before the network call
# ---------------------------------------------------------------------------
def add_notification(notif_id: str, incident_id: str, channel: str, phase: int,
                     severity: str, title: str, body: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO notifications (id, incident_id, channel, phase,"
            " status, severity, title, body, attempts, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,0,?)",
            (notif_id, incident_id, channel, phase, "pending", severity, title,
             body, now()),
        )
        conn.commit()


def finish_notification(notif_id: str, status: str, external_id: str | None = None,
                        error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE notifications SET status=?, external_id=?, error=?,"
            " attempts=COALESCE(attempts,0)+1, sent_at=? WHERE id=?",
            (status, external_id, (error or "")[:300], now(), notif_id),
        )
        conn.commit()


def notifications_for(incident_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE incident_id=? ORDER BY phase, created_at",
            (incident_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def failed_notifications(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE status='failed' ORDER BY created_at DESC"
            " LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_phase(incident_id: str, phase: int, escalation_due_at: float | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE incidents SET notify_phase=?, escalation_due_at=? WHERE id=?",
            (phase, escalation_due_at, incident_id),
        )
        conn.commit()


def mark_acked(incident_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE incidents SET acked_at=?, escalation_due_at=NULL WHERE id=?",
            (now(), incident_id),
        )
        conn.commit()


def due_escalations(now_epoch: float, limit: int = 10) -> list[dict]:
    """Incidents that notified and were never acknowledged.

    A silent phone is the failure this table exists to prevent.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT i.*, (SELECT severity FROM decisions d WHERE d.incident_id = i.id"
            " ORDER BY d.phase DESC LIMIT 1) AS severity"
            " FROM incidents i WHERE i.escalation_due_at IS NOT NULL"
            " AND i.escalation_due_at <= ? AND i.acked_at IS NULL"
            " AND i.status IN ('notified','acknowledged')"
            " ORDER BY i.escalation_due_at LIMIT ?",
            (now_epoch, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def acks_for(incident_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM acks WHERE incident_id=? ORDER BY id DESC",
                            (incident_id,)).fetchall()
    return [dict(r) for r in rows]


def recent_history(exclude_id: str | None = None, limit: int = 8) -> list[dict]:
    """The household's last few events, as compact facts for the packet.

    Deliberately small: the model needs to know this is the fourth door event
    tonight, not the entire history of the flat.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.type, e.zone_id, e.ts, e.suppressed,"
            " (SELECT a.action FROM acks a WHERE a.incident_id = e.incident_id"
            "  ORDER BY a.id DESC LIMIT 1) AS outcome"
            " FROM events e WHERE e.incident_id IS NOT ?"
            " ORDER BY e.id DESC LIMIT ?",
            (exclude_id, limit),
        ).fetchall()
    return [{"type": r["type"], "zone": r["zone_id"], "at": r["ts"],
             "kept_home": bool(r["suppressed"]), "outcome": r["outcome"]}
            for r in rows]


def person_inside(incident_id: str) -> bool:
    """Did anyone remain in the house after this incident's clip?"""
    with connect() as conn:
        row = conn.execute("SELECT sam_json FROM incidents WHERE id=?",
                           (incident_id,)).fetchone()
    if not row or not row["sam_json"]:
        return False
    try:
        sam = json.loads(row["sam_json"])
    except json.JSONDecodeError:
        return False
    return bool(sam) and not sam.get("track_unavailable") and sam.get("person_count", 0) >= 1


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------
def add_memory(household_id: str, fact: str, confidence: float, source: str,
               expires_at: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO household_memory (id, household_id, fact, confidence,"
            " source, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
            (f"mem_{abs(hash(fact)) % 10**10}", household_id, fact, confidence,
             source, now(), expires_at),
        )
        conn.commit()


def memory(household_id: str = "hh_demo", limit: int = 10) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM household_memory WHERE household_id=? AND expires_at > ?"
            " ORDER BY confidence DESC, created_at DESC LIMIT ?",
            (household_id, now(), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_memory(household_id: str = "hh_demo") -> None:
    with connect() as conn:
        conn.execute("DELETE FROM household_memory WHERE household_id=?", (household_id,))
        conn.commit()




def add_checkin(checkin_id: str, incident_id: str, transcript: str, outcome: str,
                model: str, read: dict | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO checkins (id, incident_id, transcript, outcome,"
            " model, read_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (checkin_id, incident_id, transcript, outcome, model,
             json.dumps(read) if read else None, now()),
        )
        conn.commit()


def _decode_checkin(row: dict) -> dict:
    try:
        row["read"] = json.loads(row.pop("read_json", None) or "null")
    except (json.JSONDecodeError, TypeError):
        row["read"] = None
    return row


def latest_checkin(incident_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM checkins WHERE incident_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
    return _decode_checkin(dict(row)) if row else None


# ---------------------------------------------------------------------------
# check-in state: asking -> answered | no_answer | cancelled
# ---------------------------------------------------------------------------
def start_checkin(incident_id: str, prompt: str, due_at: float,
                  timeout_s: float) -> bool:
    """Claim the one check-in for this incident. Returns False if one exists."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE incidents SET checkin_status='asking', checkin_prompt=?,"
            " checkin_due_at=?, checkin_started_at=?, checkin_answered_at=NULL,"
            " checkin_timeout_s=?"
            " WHERE id=? AND (checkin_status IS NULL OR checkin_status IN ('failed','cancelled'))",
            (prompt, due_at, time.time(), timeout_s, incident_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def mark_checkin_answered(incident_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE incidents SET checkin_status='answered', checkin_due_at=NULL,"
            " checkin_answered_at=? WHERE id=?",
            (now(), incident_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def mark_checkin_no_answer(incident_id: str) -> bool:
    """Atomically claim the timeout so two loops cannot both send the escalation."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE incidents SET checkin_status='no_answer', checkin_due_at=NULL"
            " WHERE id=? AND checkin_status='asking'",
            (incident_id,),
        )
        conn.commit()
    return bool(cur.rowcount)


def cancel_open_checkins(reason: str = "paused") -> int:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE incidents SET checkin_status='cancelled', checkin_due_at=NULL"
            " WHERE checkin_status='asking'"
        )
        conn.commit()
    return int(cur.rowcount)


def cancel_checkin(incident_id: str, reason: str = "paused") -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE incidents SET checkin_status='cancelled', checkin_due_at=NULL"
            " WHERE id=? AND checkin_status='asking'",
            (incident_id,),
        )
        conn.commit()
    return bool(cur.rowcount)


def due_checkins(now_ts: float, limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents WHERE checkin_status='asking'"
            " AND checkin_due_at IS NOT NULL AND checkin_due_at <= ?"
            " ORDER BY checkin_due_at LIMIT ?",
            (now_ts, limit),
        ).fetchall()
    return [_decode_incident(dict(r)) for r in rows]


def checkin_pending(incident_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT checkin_status FROM incidents WHERE id=?", (incident_id,)
        ).fetchone()
    return bool(row and row["checkin_status"] == "asking")


def checkins(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM checkins ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_decode_checkin(dict(r)) for r in rows]


def stats(days: int = 7) -> dict:
    """Dashboard totals for the current retention window."""
    with connect() as conn:
        events = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        suppressed = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE suppressed=1").fetchone()["c"]
        noticed = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE suppressed=0"
            " AND severity IN ('urgent','emergency')").fetchone()["c"]
        dec = conn.execute(
            "SELECT COUNT(*) n, AVG(latency_ms) mean_latency, MAX(latency_ms) max_latency"
            " FROM decisions"
        ).fetchone()
        lat = [r["latency_ms"] for r in conn.execute(
            "SELECT latency_ms FROM decisions WHERE latency_ms > 0 ORDER BY latency_ms"
        ).fetchall()]
        fails = conn.execute(
            "SELECT COUNT(*) c FROM decisions WHERE violations_json LIKE '%enrich_failed%'"
        ).fetchone()["c"]
        acks = conn.execute("SELECT COUNT(*) c FROM acks").fetchone()["c"]
        outs = conn.execute(
            "SELECT COALESCE(SUM(bytes),0) b, COUNT(*) n FROM outbound_requests"
        ).fetchone()
    p50 = lat[len(lat) // 2] if lat else 0
    p95 = lat[max(0, int(len(lat) * 0.95) - 1)] if lat else 0
    return {
        "events_seen": events,
        "alerts_sent": noticed,
        "stayed_home": suppressed,
        "restraint_rate": round(suppressed / events, 3) if events else 0.0,
        "latency_ms": {"p50": p50, "p95": p95, "mean": round(dec["mean_latency"] or 0),
                       "max": dec["max_latency"] or 0, "n": dec["n"]},
        "degraded_decisions": fails,
        "acks": acks,
        "outbound": {"bytes": outs["b"], "rows": outs["n"]},
    }



# ---------------------------------------------------------------------------
# household — the profile the floor reads
# ---------------------------------------------------------------------------
def get_household(household_id: str = "hh_demo") -> dict:
    """The household row, created on first read so a fresh DB is usable."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM households WHERE id = ?",
                           (household_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO households (id, name, timezone, created_at) VALUES (?,?,?,?)",
                (household_id, "Margaret's home", "Africa/Cairo", now()))
            row = conn.execute("SELECT * FROM households WHERE id = ?",
                               (household_id,)).fetchone()
        out = dict(row)
        state = conn.execute("SELECT value FROM household_state WHERE key='profile'").fetchone()
        if state and state["value"]:
            try:
                out["profile_overrides"] = json.loads(state["value"])
            except json.JSONDecodeError:
                out["profile_overrides"] = {}
    return out


def set_household(household_id: str, patch: dict) -> None:
    """Persist profile overrides so a restart does not forget the resident."""
    with connect() as conn:
        row = conn.execute("SELECT value FROM household_state WHERE key='profile'").fetchone()
        current = {}
        if row and row["value"]:
            try:
                current = json.loads(row["value"])
            except json.JSONDecodeError:
                current = {}
        current.update(patch.get("profile") or {})
        conn.execute(
            "INSERT OR REPLACE INTO household_state (key, value, updated_at) VALUES (?,?,?)",
            ("profile", json.dumps(current), now()))


def get_state(key: str, default=None):
    """One JSON value from household_state, or `default` if it was never set."""
    with connect() as conn:
        row = conn.execute("SELECT value FROM household_state WHERE key=?",
                           (key,)).fetchone()
    if not row or not row["value"]:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def set_state(key: str, value) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO household_state (key, value, updated_at) VALUES (?,?,?)",
            (key, json.dumps(value), now()))
        conn.commit()


def latest_still() -> dict | None:
    """The most recent kept still, for the camera page."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, still_path, thumb_path, created_at, local_time, zone_id, type"
            " FROM incidents WHERE still_path IS NOT NULL AND still_path != ''"
            " ORDER BY created_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def zones(household_id: str = "hh_demo") -> list[dict]:
    """Stored zone geometry for the household."""
    path = Path("config/edge.yaml")
    if not path.exists():
        return []
    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    return [{"name": z.get("name"), "type": z.get("type"),
             "private": bool(z.get("private")),
             "rest": bool(z.get("rest")),
             "points": z.get("points") or []}
            for z in raw.get("zones") or []]
