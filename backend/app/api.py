"""FastAPI service: ingest, policy, alerts, media, wall, setup, and privacy routes. The deterministic floor runs before any model; templates are the fail-open path."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from . import audio as audio_util
from . import clip as clip_util
from . import db, notify, policy, privacy, render, setup as setup_service, tts, wall, watch
from .demo import router as demo_router
from .push_routes import router as push_router
from .session_routes import router as session_router
from .agent import EvidenceAgent
from .meta import (SAM_MODEL, SPARK_MODEL, TRANSCRIBE_MODEL, MetaClient,
                   MetaError, parse_sam, track_features)
from .prompt import (CHECKIN_READ_PROMPT, CHECKIN_READ_SCHEMA, CONTEXT_READ_PROMPT,
                     CONTEXT_READ_SCHEMA, DIGEST_PROMPT,
                     DIGEST_SCHEMA, NIGHT_NOTE_PROMPT,
                     NIGHT_NOTE_SCHEMA, build_packet)

log = logging.getLogger("aegis.api")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)-12s %(message)s",
                    datefmt="%H:%M:%S")

ROOT = Path(__file__).resolve().parents[2]
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "data/media"))
# The replay lab lists whatever is in this folder. It never opens the files;
# the browser asks for one and plays it. Analysis, when it exists, belongs to
# the edge process, not to the page that names the clips.
EDGE_TOKEN = os.environ.get("EDGE_TOKEN", "")
TZ = ZoneInfo(os.environ.get("HOUSEHOLD_TZ", "Africa/Cairo"))
PROMPT_VERSION = "aegis-v4-objects"
HOUSEHOLD = "hh_demo"
# The spoken check-in does not replace the first alert; it is a second channel
# running alongside it. Fifteen seconds is long enough to hear the prompt and
# answer, short enough that silence still reaches a phone before phase 2.
CHECKIN_TIMEOUT_S = float(os.environ.get("CHECKIN_TIMEOUT_S", "15"))
CHECKIN_TTS = os.environ.get("CHECKIN_TTS", "on").lower() != "off"
VISITOR_STATE_KEY = "expected_visitor"
# The edge sends a heartbeat every 10s. Three missed intervals is the point at
# which "online" stops being a fact and starts being a hope.
HEARTBEAT_GRACE_S = 30

app = FastAPI(title="Aegis", version="0.2.0")

# The dashboard runs on another port in development and on the same origin in
# production. Anything else is not invited: a care system that answers
# `Access-Control-Allow-Origin: *` will happily hand a clip to any page the
# family browses.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get(
        "DASHBOARD_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",") if o],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

db.init()
app.include_router(demo_router)
app.include_router(push_router)
app.include_router(session_router)

_subscribers: set[asyncio.Queue] = set()
_client: MetaClient | None = None
PROFILE = policy.ResidentProfile()


def _load_profile() -> None:
    """Restore the resident profile after an API restart.

    The onboarding fields live in household_state; without this the in-memory
    profile reset to Margaret even when the database remembered someone else.
    """
    overrides = (db.get_household(HOUSEHOLD) or {}).get("profile_overrides") or {}
    if not isinstance(overrides, dict):
        return
    for key, value in overrides.items():
        if hasattr(PROFILE, key):
            setattr(PROFILE, key, value)


def meta() -> MetaClient:
    global _client
    if _client is None:
        key = os.environ.get("META_API_KEY", "")
        if not key:
            raise MetaError("META_API_KEY is not set")
        _client = MetaClient(key)
    return _client


def now_local() -> datetime:
    return datetime.now(TZ)


def incident_hour(incident: dict) -> int:
    """The hour the incident happened, not the hour we got around to it.

    Evaluating a replayed 03:12 door event at 14:00 with the wall clock drops
    every night rule and quietly turns an urgent night exit into a daytime
    watch. The incident carries its own local time; use it.
    """
    stamp = str(incident.get("local_time") or incident.get("started_at") or "")
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).hour
    except ValueError:
        return now_local().hour


def incident_clock(incident: dict) -> str:
    """Local wall-clock text for an incident."""
    stamp = str(incident.get("local_time") or incident.get("started_at") or "")
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return now_local().strftime("%H:%M")


def current_plan() -> dict:
    return watch.normalise(db.get_state("watch_plan"))


def effective_floor(incident: dict, local_hour: int | None = None) -> tuple[policy.Floor, str | None]:
    """The floor, after the household's watch plan has had its say.

    Every path that decides whether to interrupt goes through here - ingest,
    phase 1, phase 2, fail-open, the control room - so the plan cannot be
    honoured on one path and forgotten on another. That asymmetry would be
    invisible until the night a muted class fired anyway.
    """
    incident = with_expected_visitor(incident)
    hour = incident_hour(incident) if local_hour is None else local_hour
    plan = current_plan()
    floor = policy.evaluate(incident, PROFILE, local_hour=hour)
    return watch.apply(plan, incident, floor, hour)


# ---------------------------------------------------------------------------
# expected visitors: a household intention, evaluated at incident ingest
# ---------------------------------------------------------------------------
def visitor_state() -> dict:
    raw = db.get_state(VISITOR_STATE_KEY, {})
    if not isinstance(raw, dict):
        return {"expected": False}
    until = float(raw.get("until") or 0)
    left = int(max(0, until - time.time()))
    if left <= 0:
        return {"expected": False}
    return {
        "expected": True,
        "until": datetime.fromtimestamp(until, TZ).isoformat(),
        "minutes_left": left // 60,
        "by": str(raw.get("by") or "family"),
        "label": str(raw.get("label") or "visitor"),
    }


def visitor_expected_now() -> bool:
    return bool(visitor_state().get("expected"))


def with_expected_visitor(incident: dict) -> dict:
    """Fold the household's expected-visitor window into an incident.

    The edge can only see people, not the family's calendar. A caregiver marked
    expected for the evening should turn a second-person event into a quiet row,
    not a 3 AM phone call. This runs before the floor, so every path sees it.
    """
    if incident.get("type") != "unexpected_presence":
        return incident
    state = visitor_state()
    if not state.get("expected"):
        return incident
    sensors = dict(incident.get("sensors") or {})
    sensors["expected_visitor"] = True
    sensors["visitor_expected_by"] = state.get("by")
    return {**incident, "sensors": sensors}


class VisitorIn(BaseModel):
    minutes: int = 120
    by: str = "family"
    label: str = "caregiver"


@app.get("/api/visitors/expected")
def get_visitor_expected():
    return visitor_state()


@app.post("/api/visitors/expected")
async def set_visitor_expected(body: VisitorIn):
    if not 5 <= body.minutes <= 720:
        raise HTTPException(422, "minutes must be between 5 and 720")
    until = time.time() + body.minutes * 60
    db.set_state(VISITOR_STATE_KEY, {
        "until": until, "by": body.by[:40], "label": body.label[:80],
    })
    out = visitor_state()
    await broadcast({"type": "visitor.expected", **out})
    log.info("expected visitor for %d minutes by %s", body.minutes, body.by[:40])
    return out


@app.delete("/api/visitors/expected")
async def clear_visitor_expected():
    db.set_state(VISITOR_STATE_KEY, {})
    out = {"expected": False}
    await broadcast({"type": "visitor.expected", **out})
    return out


# ---------------------------------------------------------------------------
# spoken check-in and the silence timer
# ---------------------------------------------------------------------------
def checkin_prompt_for(incident: dict) -> str:
    name = (PROFILE.display_name or "there").split()[0]
    if str(incident.get("type")) == "help_call":
        return (f"{name}, it's Aegis. I heard you call for help. Are you okay? "
                "Say I'm okay, or say help.")
    if incident.get("type") == "fall_suspected":
        return (f"{name}, it's Aegis. I noticed you may have fallen. Are you okay? "
                "Say I'm okay, or say help.")
    if str(incident.get("type", "")).startswith("fall"):
        return (f"{name}, it's Aegis. I noticed you are on the floor. Are you okay? "
                "Say I'm okay, or say help.")
    return f"{name}, it's Aegis. Are you okay? Say I'm okay, or say help."


async def start_checkin(incident_id: str) -> dict | None:
    """Ask, then wait. Never blocks phase 1 and never runs after a pause.

    The audio is rendered locally first, so the prompt is not waiting on an
    uplink. If TTS is unavailable the text still goes out and the dashboard can
    speak it with the browser's own voice.
    """
    incident = db.get_incident(incident_id)
    if not incident:
        return None
    if privacy.paused_state().get("paused"):
        return None
    if incident.get("checkin_status") in ("asking", "answered", "no_answer", "cancelled"):
        return None
    if not policy.should_checkin(incident):
        return None

    prompt = checkin_prompt_for(incident)
    audio_url = None
    if CHECKIN_TTS:
        target = MEDIA_ROOT / incident_id / "checkin_prompt.wav"
        try:
            ok = await asyncio.to_thread(tts.speak_to_wav, prompt, target)
        except Exception as exc:  # noqa: BLE001
            log.warning("checkin tts failed for %s: %s", incident_id, exc)
            ok = False
        if ok:
            audio_url = auth.media_url(incident_id, "checkin_prompt.wav")

    due_at = time.time() + CHECKIN_TIMEOUT_S
    if not db.start_checkin(incident_id, prompt, due_at, CHECKIN_TIMEOUT_S):
        if audio_url:
            (MEDIA_ROOT / incident_id / "checkin_prompt.wav").unlink(missing_ok=True)
        return None
    await broadcast({"type": "checkin.started", "id": incident_id, "prompt": prompt,
                     "audio_url": audio_url, "due_at": due_at,
                     "timeout_s": CHECKIN_TIMEOUT_S})
    log.info("checkin %s asking (%s) tts=%s", incident_id,
             "help_call" if incident.get("type") == "help_call" else incident.get("type"),
             bool(audio_url))
    return {"prompt": prompt, "audio_url": audio_url, "due_at": due_at,
            "timeout_s": CHECKIN_TIMEOUT_S}


def _checkin_alert(incident: dict, severity: str, title: str, body: str) -> dict:
    return {
        "incident_id": incident["id"],
        "title": title[:60],
        "body": body[:240],
        "severity": severity,
    }


async def expire_checkin(incident: dict) -> None:
    """The silence path: mark once, escalate one step, and tell the family."""
    incident_id = incident["id"]
    full = db.get_incident(incident_id) or incident
    if full.get("checkin_status") != "asking":
        return
    if privacy.paused_state().get("paused"):
        if db.cancel_checkin(incident_id, "paused"):
            await broadcast({"type": "checkin.cancelled", "id": incident_id,
                             "reason": "monitoring_paused"})
        return
    if full.get("status") in ("resolved", "acknowledged"):
        db.cancel_checkin(incident_id, "already_resolved")
        return
    # Claim before any network call; two loops must not send two escalations.
    if not db.mark_checkin_no_answer(incident_id):
        return

    floor, _ = effective_floor(full)
    current = (full.get("decision") or {}).get("severity") or full.get("severity") or floor.severity
    new = policy.raise_one(policy.max_severity(current, floor.severity))
    name = (PROFILE.display_name or "the resident").split()[0]
    after_fall = str(full.get("type", "")).startswith("fall")
    if after_fall:
        # The latest decision is what the event list and the lock screen show.
        # A row that says only "No answer" loses the reason the check-in was
        # asked; keep the fall in the escalation itself.
        title = f"{name} may have fallen — no answer"
        body = (f"{name} has not answered the check-in after a possible fall. "
                "Please call her now.")
    else:
        title = f"No answer from {name}"
        body = (f"{name} did not answer the check-in within "
                f"{int(CHECKIN_TIMEOUT_S)} seconds. Please call her now.")
    decision = policy.Decision(
        severity=new, notify_now=True, title=title, body=body,
        evidence=["no_answer", "checkin_timeout"],
        actions=[{"id": "call_resident", "label": "Call now"},
                 {"id": "send_help", "label": "Send help"},
                 {"id": "im_checking", "label": "I'm checking"}],
        confidence=1.0, follow_up_s=0, escalate_if_no_ack=False,
        rationale=("The spoken check-in was not answered before its deadline. "
                   "Silence escalates one step; it never lowers the alert."),
    )
    db.add_decision(incident_id, 3, floor.severity, floor.reasons, decision,
                    "checkin_timer", PROMPT_VERSION, [], 0)
    db.set_status(incident_id, "notified", new)
    db.add_checkin(f"chk_{incident_id[-8:]}_{int(time.time())}", incident_id,
                   f"No answer within {int(CHECKIN_TIMEOUT_S)} seconds",
                   "no_response", "timer")
    db.record_event(incident_id, full.get("camera_id"), "checkin_no_response",
                    full.get("zone_id"), db.now(), new, suppressed=False,
                    reason="no_answer", source="checkin")

    acked = bool(full.get("acked_at"))
    if not acked:
        await notify.dispatch(_checkin_alert(full, new, title, body), 3, broadcast)
        db.mark_phase(incident_id, 3)
    await broadcast({"type": "checkin.no_answer", "id": incident_id,
                     "severity": new, "escalated": not acked,
                     "title": title})
    log.warning("checkin %s no answer -> %s%s", incident_id, new,
                " (already acknowledged)" if acked else "")


async def checkin_loop() -> None:
    """A deadline is a promise; this is the loop that keeps it."""
    while True:
        try:
            for incident in db.due_checkins(time.time()):
                await expire_checkin(incident)
        except Exception as exc:  # noqa: BLE001
            log.exception("checkin loop error: %s", exc)
        await asyncio.sleep(1.0)


async def read_ambiguous_context(incident_id: str) -> None:
    """Read a cry or ambiguous sound against the room, then apply the floor."""
    incident = db.get_incident(incident_id)
    if not incident or incident.get("type") != "help_call":
        return
    if incident.get("checkin_status") not in (None, "asking"):
        return
    floor, _ = effective_floor(incident)
    if floor.severity != "watch":
        return
    if incident.get("context_read"):
        return

    packet = build_packet(
        incident, incident.get("track") or {}, floor, PROFILE,
        local_hour=incident_hour(incident),
        history=db.recent_history(incident_id),
        memory=db.memory(HOUSEHOLD),
        quiet=policy.in_quiet_hours(incident_hour(incident)),
    )
    started = time.time()
    try:
        raw, usage = await asyncio.to_thread(
            meta().decide, packet, CONTEXT_READ_PROMPT, CONTEXT_READ_SCHEMA, "low",
        )
    except (MetaError, ValueError, KeyError) as exc:
        log.warning("context read failed for %s (%s); keeping the check-in", incident_id, exc)
        return
    latency_ms = int((time.time() - started) * 1000)

    source = str(raw.get("source") or "unclear")
    if source not in ("person", "media", "unclear"):
        source = "unclear"
    severity = str(raw.get("severity") or floor.severity)
    if severity not in policy.ORDER:
        severity = floor.severity
    severity = policy.max_severity(severity, floor.severity)
    notify_now = bool(raw.get("notify_now")) and policy.ORDER[severity] >= policy.ORDER["urgent"]
    name = (PROFILE.display_name or "the resident").split()[0]
    title = str(raw.get("title") or "").strip()[:60]
    body = str(raw.get("body") or "").strip()[:240]
    read = str(raw.get("read") or "").strip()[:160]
    low = f"{title} {body}".lower()
    if not title or not body or any(term in low for term in policy.FORBIDDEN_TERMS):
        title = f"A cry in the room" if source != "media" else "A sound in the room"
        body = (f"{name} called out. A television or radio was on, so someone "
                f"should check whether it was her." if source != "media" else
                f"The sound was most likely the television or radio. Aegis is asking {name} now.")
    gate = policy.EvidenceGate(packet, extra=set(incident.get("evidence") or []))
    evidence = [str(e) for e in (raw.get("evidence") or []) if str(e) in gate][:6]
    record = {
        "source": source,
        "severity": severity,
        "notify_now": notify_now,
        "title": title,
        "body": body,
        "read": read or f"The room is ambiguous: a cry, a person in frame, and a television on.",
        "evidence": evidence,
        "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 0.5))),
        "model": SPARK_MODEL,
        "latency_ms": latency_ms,
    }
    db.attach_context_read(incident_id, record)
    db.add_outbound(incident_id, "api.meta.ai", "context_arbiter",
                    len(json.dumps(packet)), usage.output_tokens, "30d", False,
                    latency_ms=usage.latency_ms, model=SPARK_MODEL)
    await broadcast({"type": "context.read", "id": incident_id, **record})
    log.info("context read %s source=%s severity=%s in %dms",
             incident_id, source, severity, latency_ms)

    # The model may finish after she has already answered or the household has
    # paused. The read is still stored for the "what I checked" panel, but it
    # must not page anyone over a question that is already closed.
    fresh = db.get_incident(incident_id) or incident
    if (fresh.get("checkin_status") in ("answered", "cancelled")
            or fresh.get("status") == "resolved"):
        return

    if notify_now:
        current = (incident.get("decision") or {}).get("severity") or incident.get("severity") or floor.severity
        if policy.ORDER[severity] <= policy.ORDER.get(current, 0):
            return
        actions = ([{"id": "call_resident", "label": "Call now"},
                    {"id": "send_help", "label": "Send help"},
                    {"id": "im_checking", "label": "I'm checking"}]
                   if source == "person" else
                   [{"id": "call_resident", "label": "Call now"},
                    {"id": "im_checking", "label": "I'm checking"},
                    {"id": "false_alarm", "label": "False alarm"}])
        decision = policy.Decision(
            severity=severity, notify_now=True, title=title, body=body,
            evidence=evidence, actions=actions, confidence=record["confidence"],
            follow_up_s=60, escalate_if_no_ack=True,
            rationale=record["read"],
        )
        db.add_decision(incident_id, 3, floor.severity, floor.reasons, decision,
                        SPARK_MODEL, PROMPT_VERSION, [], latency_ms)
        db.set_status(incident_id, "notified", severity)
        if not incident.get("acked_at"):
            await notify.dispatch(_checkin_alert(incident, severity, title, body), 3, broadcast)
            db.mark_phase(incident_id, 3)


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def verify_edge(timestamp: str | None, signature: str | None, body: bytes) -> None:
    """HMAC when EDGE_TOKEN is configured; open on a demo machine, and it says so."""
    if not EDGE_TOKEN:
        return
    if not timestamp or not signature:
        raise HTTPException(401, "missing signature")
    try:
        if abs(time.time() - int(timestamp)) > 300:
            raise HTTPException(401, "clock skew")
    except ValueError:
        raise HTTPException(401, "bad timestamp") from None
    expected = hmac.new(EDGE_TOKEN.encode(), timestamp.encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "bad signature")


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------
class IncidentIn(BaseModel):
    incident_id: str
    household_id: str = "hh_demo"
    camera_id: str = "cam_hallway"
    type: str
    status: str = "detected"
    started_at: str | None = None
    local_time: str | None = None
    zone_id: str | None = None
    severity_hint: str | None = None
    track: dict[str, Any] = Field(default_factory=dict)
    fall: dict[str, Any] | None = None
    sensors: dict[str, Any] = Field(default_factory=dict)
    privacy: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    source: str = "live"


@app.post("/api/ingest/incident")
async def ingest_incident(request: Request,
                          x_edge_timestamp: str | None = Header(None),
                          x_edge_signature: str | None = Header(None)):
    body = await request.body()
    verify_edge(x_edge_timestamp, x_edge_signature, body)
    payload = IncidentIn(**json.loads(body))
    if payload.type == "unexpected_presence" and visitor_expected_now():
        payload.sensors = {**(payload.sensors or {}), "expected_visitor": True}

    # A paused household still records that something was noticed, but nothing
    # is escalated and nothing leaves the home. The pause must win over policy.
    pause = privacy.paused_state()
    if pause.get("paused"):
        db.record_event(payload.incident_id, payload.camera_id, payload.type,
                        payload.zone_id, payload.local_time or db.now(), "info",
                        suppressed=True, reason="monitoring_paused",
                        source=payload.source)
        log.info("ingest %s ignored: paused by %s", payload.incident_id, pause.get("by"))
        return {"ok": True, "id": payload.incident_id, "floor": "info",
                "paused": True}

    db.upsert_incident(payload.model_dump())

    # The event stream records everything, including what policy will suppress.
    floor, plan_note = effective_floor(payload.model_dump())
    db.raise_incident_severity(payload.incident_id, floor.severity)
    db.record_event(payload.incident_id, payload.camera_id, payload.type,
                    payload.zone_id, payload.local_time or db.now(),
                    floor.severity, suppressed=not floor.notifies,
                    reason=",".join(floor.reasons), source=payload.source)

    await broadcast({"type": "incident.created", "id": payload.incident_id,
                     "incident_type": payload.type, "floor": floor.severity})
    log.info("ingest %s %s floor=%s", payload.incident_id, payload.type, floor.severity)

    # Phase 1: notify from the floor immediately, without waiting for the model.
    asyncio.create_task(phase_one(payload.incident_id))
    if policy.should_checkin(payload.model_dump()):
        asyncio.create_task(start_checkin(payload.incident_id))
    if payload.type == "help_call":
        asyncio.create_task(read_ambiguous_context(payload.incident_id))
    return {"ok": True, "id": payload.incident_id, "floor": floor.severity}


@app.post("/api/ingest/audio")
async def ingest_audio(
    audio: UploadFile = File(...),
    incident_id: str = Form(""),
    household_id: str = Form("hh_demo"),
    camera_id: str = Form("cam_hallway"),
    zone_id: str = Form(""),
    local_time: str = Form(""),
    sensors: str = Form("{}"),
    track: str = Form("{}"),
    hint: str = Form(""),
    event_type: str = Form("help_call"),
    source: str = Form("live"),
    x_edge_timestamp: str | None = Header(None),
    x_edge_signature: str | None = Header(None),
):
    """Accept an event-gated WAV, transcribe it, and delete the audio."""
    verify_edge(x_edge_timestamp, x_edge_signature, incident_id.encode())
    if event_type != "help_call":
        raise HTTPException(422, "only help_call is accepted on this endpoint")
    if not incident_id:
        incident_id = f"aud_{uuid.uuid4().hex[:16]}"
    try:
        sensors_obj = json.loads(sensors or "{}")
        track_obj = json.loads(track or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"bad sensors/track JSON: {exc}") from exc
    if not isinstance(sensors_obj, dict) or not isinstance(track_obj, dict):
        raise HTTPException(422, "sensors and track must be objects")

    work = MEDIA_ROOT / incident_id
    work.mkdir(parents=True, exist_ok=True)
    raw = work / "event_audio"
    body = await audio.read()
    raw.write_bytes(body)
    try:
        report, wav = await asyncio.to_thread(audio_util.analyse, raw, work, hint)
    except Exception as exc:  # noqa: BLE001
        raw.unlink(missing_ok=True)
        raise HTTPException(422, f"could not read audio: {exc}") from exc

    started = time.time()
    transcript = ""
    try:
        transcript = await asyncio.to_thread(meta().transcribe, wav)
    except MetaError as exc:
        # A failed transcript must not silently turn a cry into silence. The
        # loudness/hint assessment still goes forward and policy stays cautious.
        log.warning("audio transcribe failed for %s: %s", incident_id, exc)
    latency_ms = int((time.time() - started) * 1000)
    measured = audio_util.Levels(
        report["duration_s"], report["rms_dbfs"], report["peak_dbfs"],
        report["loud_ratio"], report["scream_like"],
    )
    assessment = audio_util.assess(transcript, measured, hint)
    sensors_obj["audio"] = assessment
    sensors_obj.setdefault("media", {"verified": False})
    if "person_count" not in track_obj and "person_present" in sensors_obj:
        track_obj["person_count"] = 1 if sensors_obj.get("person_present") else 0
    if "posture" in sensors_obj and "posture" not in track_obj:
        track_obj["posture"] = sensors_obj["posture"]

    local = local_time or now_local().isoformat()
    packet = {
        "incident_id": incident_id,
        "household_id": household_id,
        "camera_id": camera_id,
        "type": "help_call",
        "status": "detected",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "local_time": local,
        "zone_id": zone_id or None,
        "track": track_obj,
        "sensors": sensors_obj,
        "privacy": {"masks_applied": [], "audio": True, "raw_video_left_home": False},
        "artifacts": {},
        "evidence": ["call_for_help", f"audio_{assessment['kind']}"] + (
            ["media_tv_on"] if (sensors_obj.get("media") or {}).get("tv_on") else []
        ) + (
            ["media_radio_on"] if (sensors_obj.get("media") or {}).get("radio_on") else []
        ),
        "source": source,
    }
    db.upsert_incident(packet)
    floor, plan_note = effective_floor(packet)
    db.raise_incident_severity(incident_id, floor.severity)
    db.add_outbound(incident_id, "api.meta.ai", "audio_distress", len(body), 0, "1h",
                    False, latency_ms=latency_ms, model=TRANSCRIBE_MODEL,
                    contains_audio=True)
    floor, plan_note = effective_floor(packet)
    db.record_event(incident_id, camera_id, "help_call", zone_id or None, local,
                    floor.severity, suppressed=not floor.notifies,
                    reason=",".join(floor.reasons), source=source)
    await broadcast({"type": "incident.created", "id": incident_id,
                     "incident_type": "help_call", "floor": floor.severity,
                     "audio": bool(assessment.get("distress"))})
    asyncio.create_task(phase_one(incident_id))
    if policy.should_checkin(packet):
        asyncio.create_task(start_checkin(incident_id))
    asyncio.create_task(read_ambiguous_context(incident_id))
    log.info("audio %s floor=%s distress=%s%s", incident_id, floor.severity,
             assessment.get("distress"), f", {plan_note}" if plan_note else "")
    # The raw audio is not kept: the transcript and assessment are the record.
    raw.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    return {"ok": True, "id": incident_id, "floor": floor.severity,
            "distress": assessment.get("distress"), "audio": assessment,
            "plan": plan_note}


@app.post("/api/ingest/clip")
async def ingest_clip(incident_id: str = Form(""), clip: UploadFile | None = File(None),
                      still: UploadFile | None = File(None), thumb: UploadFile | None = File(None),
                      x_edge_timestamp: str | None = Header(None),
                      x_edge_signature: str | None = Header(None)):
    verify_edge(x_edge_timestamp, x_edge_signature, incident_id.encode())
    if not incident_id or clip is None:
        raise HTTPException(422, "incident_id and clip are required")
    out = MEDIA_ROOT / incident_id
    out.mkdir(parents=True, exist_ok=True)
    size = 0
    paths: dict[str, str] = {}
    for name, upload in (("clip.mp4", clip), ("still.jpg", still), ("thumb.jpg", thumb)):
        if upload is None:
            continue
        data = await upload.read()
        (out / name).write_bytes(data)
        paths[name] = str(out / name)
        if name == "clip.mp4":
            size = len(data)
    db.attach_clip(incident_id, paths.get("clip.mp4", ""), paths.get("still.jpg"),
                   paths.get("thumb.jpg"), size)
    await broadcast({"type": "incident.clip", "id": incident_id})
    log.info("clip %s (%d KB)", incident_id, size // 1024)
    return {"ok": True}


@app.post("/api/ingest/heartbeat")
async def ingest_heartbeat(request: Request,
                           x_edge_timestamp: str | None = Header(None),
                           x_edge_signature: str | None = Header(None)):
    body = await request.body()
    verify_edge(x_edge_timestamp, x_edge_signature, body)
    db.add_heartbeat(json.loads(body))
    return {"ok": True}


# --------------------------------------------------------------------------
# read API
# --------------------------------------------------------------------------
def with_media(inc: dict) -> dict:
    """Attach short-lived signed media URLs and report whether a clip exists."""
    for key, name in (("clip_path", "clip.mp4"), ("still_path", "still.jpg"),
                      ("thumb_path", "thumb.jpg")):
        if inc.get(key):
            inc[key.replace("_path", "_url")] = auth.media_url(inc["id"], name)
        inc.pop(key, None)
    # The spoken check-in question is served from the same signed media path as
    # the clip. It is local speech, not an outbound TTS request.
    if (MEDIA_ROOT / inc["id"] / "checkin_prompt.wav").exists():
        inc["checkin_audio_url"] = auth.media_url(inc["id"], "checkin_prompt.wav")
    # The UI asks "is there a clip to play?", not "where is it on disk".
    inc["has_clip"] = bool(inc.get("clip_url"))
    if (MEDIA_ROOT / inc["id"] / "annotated.mp4").exists():
        inc["annotated_url"] = auth.media_url(inc["id"], "annotated.mp4")
    return inc


@app.get("/api/incidents")
def list_incidents(limit: int = 50, status: str | None = None,
                   severity: str | None = None, type: str | None = None,
                   since: str | None = None):
    items = db.list_incidents(limit, status)
    if severity:
        items = [i for i in items if i.get("severity") == severity]
    if type:
        items = [i for i in items if i.get("type") == type]
    if since:
        items = [i for i in items if str(i.get("created_at") or "") >= since]
    return {"items": [with_media(i) for i in items]}


@app.get("/api/household")
def get_household():
    """Profile, zones and privacy settings in one read for the dashboard."""
    hh = db.get_household(HOUSEHOLD)
    return {
        "household": hh,
        "profile": {
            "display_name": PROFILE.display_name, "age": PROFILE.age,
            "cognitive_condition": PROFILE.cognitive_condition,
            "wanders": PROFILE.wanders,
            "uses_mobility_aid": PROFILE.uses_mobility_aid,
            "anticoagulant": PROFILE.anticoagulant,
            "lives_alone": PROFILE.lives_alone,
            "conditions": list(PROFILE.conditions),
        },
        "zones": db.zones(HOUSEHOLD),
        "privacy": {"consent": privacy.consent_state(),
                    "pause": privacy.paused_state()},
        "timezone": str(TZ),
        "watch": watch.summary(current_plan()),
        "onboarded": bool(current_plan().get("onboarded_at")),
    }


# --------------------------------------------------------------------------
# the watch plan — what onboarding actually writes
# --------------------------------------------------------------------------
@app.get("/api/watch")
def get_watch():
    """The questionnaire and the answers so far, in one read.

    `catalog` is served from the backend rather than hardcoded in the UI for a
    reason: the list of things Aegis can watch has to match the list of things
    the policy floor can actually raise. A frontend that invents an option the
    backend has no rule for is a settings page that lies.
    """
    plan = current_plan()
    return {
        "plan": plan,
        "summary": watch.summary(plan),
        "classes": [
            {
                "id": c.id, "label": c.label, "question": c.question,
                "detail": c.detail, "tier": c.tier, "default": c.default,
                "locked": c.locked, "modes": list(c.modes if not c.locked else ("alert",)),
                "examples": c.examples, "types": list(c.types),
            }
            for c in watch.CATALOG
        ],
        "zones": db.zones(HOUSEHOLD),
        "quiet": plan["quiet"],
        "door_hours": plan["door_hours"],
        "onboarded_at": plan.get("onboarded_at"),
        "consent_at": plan.get("consent_at"),
    }


@app.put("/api/watch")
async def put_watch(body: dict):
    """Save the plan. A safety class set to `off` is rejected, not repaired."""
    try:
        plan = watch.validate({**current_plan(), **body})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.get("complete"):
        plan["onboarded_at"] = db.now()
    db.set_state("watch_plan", plan)
    await broadcast({"type": "watch.updated", "summary": watch.summary(plan)})
    log.info("watch plan updated: alert=%s digest=%s off=%s door=%s",
             plan["modes"].get("fall"), plan["modes"].get("door"),
             plan["modes"].get("motion"), plan["door_hours"])
    return {"ok": True, "plan": plan, "summary": watch.summary(plan)}


@app.get("/api/camera")
def get_camera():
    """What the camera is doing right now, and the last frame it kept.

    This is the endpoint that makes Aegis feel like a CCTV system rather than a
    video parser: liveness, framing, masks, and the zones drawn in the same
    coordinate space as the pixels. The still is the most recent one on disk,
    labelled with its own timestamp - never a placeholder.
    """
    hb = db.last_heartbeat()
    still = db.latest_still()
    # A heartbeat row is permanent, so its mere existence proves nothing about
    # now. "Online" means the camera spoke within the last three intervals; if
    # it went quiet an hour ago the page must say so rather than showing a
    # still from this morning and calling the house watched.
    online = False
    if hb and hb.get("ts"):
        try:
            seen = datetime.fromisoformat(str(hb["ts"]).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=TZ)
            online = (now_local() - seen).total_seconds() <= 3 * HEARTBEAT_GRACE_S
        except ValueError:
            online = False
    payload = {
        "camera": {"id": (hb or {}).get("camera_id") or "cam_hallway",
                   "online": online, "fps": (hb or {}).get("fps"),
                   "frames": (hb or {}).get("frames"),
                   "detector": (hb or {}).get("detector"),
                   "queue_depth": (hb or {}).get("queue_depth"),
                   "last_seen": (hb or {}).get("ts")},
        "zones": db.zones(HOUSEHOLD),
        "still": None,
        "objects": (hb or {}).get("objects") or [],
        "objects_at": (hb or {}).get("objects_at"),
        "masks": [z["name"] for z in db.zones(HOUSEHOLD) if z.get("private")],
    }
    if still:
        payload["still"] = {
            "url": auth.media_url(still["id"], "still.jpg"),
            "incident_id": still["id"], "at": still.get("local_time"),
            "type": still.get("type"), "zone": still.get("zone_id"),
        }
    return payload


@app.put("/api/household")
async def put_household(body: dict):
    """Update the resident profile. Validated, because a typo here changes a
    floor: `age: 8` instead of 81 would drop the high-risk reason."""
    allowed = {"display_name", "age", "cognitive_condition", "wanders",
               "uses_mobility_aid", "anticoagulant", "lives_alone", "conditions"}
    changed = {}
    for k, v in (body.get("profile") or {}).items():
        if k not in allowed:
            raise HTTPException(422, f"unknown profile field {k}")
        if k == "age" and not (0 < int(v) < 130):
            raise HTTPException(422, "age out of range")
        if k in ("wanders", "uses_mobility_aid", "anticoagulant", "lives_alone"):
            v = bool(v)
        if k == "conditions":
            if not isinstance(v, list) or len(v) > 12:
                raise HTTPException(422, "conditions must be a short list")
            v = [str(x)[:40] for x in v if str(x).strip()]
        setattr(PROFILE, k, v)
        changed[k] = v
    if changed:
        db.set_household(HOUSEHOLD, {"profile": changed})
        await broadcast({"type": "household.updated", "changed": changed})
    return {"ok": True, "changed": changed}


@app.post("/api/setup/scan")
async def setup_scan(frame: UploadFile = File(...)):
    """Map one setup frame locally. The frame is never sent to Meta or kept."""
    try:
        data = await frame.read(setup_service.MAX_FRAME_BYTES + 1)
        return await setup_service.map_frame(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("setup scan failed: %s", exc)
        raise HTTPException(503, "local scene detection unavailable") from exc


@app.post("/api/setup/propose")
async def setup_propose(body: dict):
    """Ask Muse to tune the watch plan from structured context, never pixels."""
    try:
        return await asyncio.to_thread(setup_service.propose_plan, body, meta())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except MetaError as exc:
        raise HTTPException(503, f"setup proposal unavailable: {exc}") from exc


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    inc = db.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "not found")
    inc["notifications"] = db.notifications_for(incident_id)
    inc["acks"] = db.acks_for(incident_id)
    return with_media(inc)


class AckIn(BaseModel):
    action: str


@app.post("/api/incidents/{incident_id}/ack")
async def ack(incident_id: str, body: AckIn):
    if body.action not in policy.ALLOWED_ACTIONS:
        raise HTTPException(422, f"unknown action {body.action}")
    inc = db.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "not found")
    db.add_ack(incident_id, body.action)
    db.mark_acked(incident_id)
    # The ack is the feedback signal: it changes the next decision for this
    # household, and it stops the escalation timer.
    await broadcast({"type": "incident.ack", "id": incident_id, "action": body.action})
    log.info("ack %s %s", incident_id, body.action)
    return {"ok": True, "action": body.action}


@app.get("/api/stats")
def stats():
    counts = db.event_counts(days=7)
    s = db.stats()
    hb = db.last_heartbeat() or {}
    return {
        **s,
        "restraint": counts,
        "heartbeat": hb,
        "ledger": db.ledger_totals(),
        "failed_notifications": len(db.failed_notifications(5)),
    }


@app.get("/api/events")
def events(limit: int = 100):
    return {"items": db.recent_events(limit), "suppressed": db.suppressed_sample(20)}


@app.get("/api/night")
def night(date: str | None = None, offset: int = 0):
    """The last 22:00–06:00 window as a small, date-browsable record."""
    if offset < -31 or offset > 0:
        raise HTTPException(422, "offset must be between -31 and 0")
    now = now_local()
    if date:
        try:
            start_day = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(422, "date must be YYYY-MM-DD") from exc
    else:
        if now.hour >= 22:
            start_day = now.date()
        else:
            start_day = now.date() - timedelta(days=1)
        start_day += timedelta(days=offset)
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=TZ).replace(hour=22)
    end = start + timedelta(hours=8)

    def inside(value) -> bool:
        try:
            stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return False
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=TZ)
        return start <= stamp.astimezone(TZ) < end

    all_events = [e for e in db.recent_events(500) if inside(e.get("ts"))]
    all_incidents = [i for i in db.list_incidents(200)
                     if inside(i.get("local_time") or i.get("created_at"))]
    door = [e for e in all_events if str(e.get("type", "")).startswith("door")]
    unexpected = [e for e in all_events if e.get("type") == "unexpected_presence"]
    falls = [e for e in all_events if str(e.get("type", "")).startswith("fall")]
    checkins = [e for e in all_events if e.get("type") == "checkin_no_response"]
    alerts = [i for i in all_incidents if i.get("severity") in ("urgent", "emergency")]
    note_key = f"night_note:{start_day.isoformat()}"
    note = db.get_state(note_key)
    if note and int(note.get("event_count", -1)) != len(all_events):
        # A note written before new events landed is stale, not a summary.
        note = None
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "active": start <= now < end,
        "events": all_events,
        "incidents": all_incidents,
        "note": note,
        "summary": {
            "events": len(all_events),
            "quiet": sum(1 for e in all_events if e.get("suppressed")),
            "alerts": len(alerts),
            "door": len(door),
            "unexpected": len(unexpected),
            "falls": len(falls),
            "checkins": len(checkins),
            "hours": 8,
        },
    }


class NightNoteIn(BaseModel):
    offset: int = 0


@app.post("/api/night/note")
async def write_night_note(body: NightNoteIn):
    """Ask Muse for a short morning note built only from the night rows."""
    view = night(offset=body.offset)
    events = [
        {
            "at": str(e.get("ts") or "")[11:16],
            "what": TYPE_NAMES.get(str(e.get("type")), "something"),
            "where": ROOM_NAMES.get(str(e.get("zone_id")), str(e.get("zone_id") or "home")),
            "severity": e.get("severity"),
            "stayed_quiet": bool(e.get("suppressed")),
            "reason": e.get("reason") or None,
        }
        for e in view["events"][:40]
    ]
    incidents = [
        {"what": TYPE_NAMES.get(str(i.get("type")), "something"),
         "severity": i.get("severity")}
        for i in view["incidents"] if i.get("severity") in ("urgent", "emergency")
    ]
    context = {
        "window": {"start": view["start"], "end": view["end"], "in_progress": view["active"]},
        "summary": view["summary"],
        "events": events,
        "alerts": incidents,
    }
    started = time.time()
    try:
        raw, usage = await asyncio.to_thread(
            meta().decide, context, NIGHT_NOTE_PROMPT, NIGHT_NOTE_SCHEMA, "low",
        )
    except MetaError as exc:
        raise HTTPException(503, f"night note unavailable: {exc}") from exc
    note = str(raw.get("note") or "").strip()[:340]
    check = str(raw.get("check") or "").strip()[:140]
    low = f"{note} {check}".lower()
    jargon = ("record", "event", "row", "data", "alert", "pipeline", "system", "monitoring")
    if (not note or any(term in low for term in policy.FORBIDDEN_TERMS)
            or any(term in low for term in jargon)):
        count = view["summary"]["events"]
        note = ("Nothing was noted in this window." if count == 0 else
                f"{count} things were noted between 22:00 and 06:00.")
        check = "Review the night page if anything here is unclear."
        model = "template"
    else:
        model = SPARK_MODEL
    state = {
        "note": note,
        "check": check,
        "model": model,
        "event_count": len(view["events"]),
        "created_at": db.now(),
    }
    key = f"night_note:{datetime.fromisoformat(view['start']).date().isoformat()}"
    db.set_state(key, state)
    db.add_outbound(None, "api.meta.ai", "night_note", len(json.dumps(context)),
                    usage.output_tokens, "30d", False,
                    latency_ms=int((time.time() - started) * 1000), model=SPARK_MODEL)
    await broadcast({"type": "night.note", "offset": body.offset, **state})
    log.info("night note written for offset %d (%s)", body.offset, model)
    return state


@app.get("/api/ledger")
def ledger():
    return {"items": db.ledger(), "totals": db.ledger_totals()}


@app.get("/api/health")
@app.get("/health")
def health():
    return {"ok": True, "last_heartbeat": db.last_heartbeat(),
            "time": db.now(), "edge_auth": bool(EDGE_TOKEN)}


@app.get("/api/memory")
def memory():
    return {"items": db.memory(HOUSEHOLD), "household": HOUSEHOLD}


# ---------------------------------------------------------------------------
# privacy controls — each one is a code path with a visible effect
# ---------------------------------------------------------------------------
class ConsentIn(BaseModel):
    member: str = "resident"
    device: str = ""
    given: bool = True


@app.get("/api/privacy")
def privacy_state():
    return {"consent": privacy.consent_state(),
            "pause": privacy.paused_state(),
            "retention": {"local_days": privacy.LOCAL_RETENTION_DAYS,
                          "upstream_hours": privacy.API_RETENTION_S // 3600},
            "ledger": db.ledger_totals()}


@app.post("/api/privacy/consent")
async def privacy_consent(body: ConsentIn):
    if not body.given:
        out = privacy.revoke_consent()
        cancelled = db.cancel_open_checkins("consent_revoked")
        await broadcast({"type": "privacy.paused", "reason": "consent_revoked",
                         "checkins_cancelled": cancelled})
        return out
    out = privacy.record_consent(body.member, body.device, body.given)
    await broadcast({"type": "privacy.consent", "member": body.member})
    return out


@app.post("/api/privacy/pause")
async def privacy_pause(body: dict | None = None):
    """Resident-initiated. The family sees a calm state, not a failure."""
    body = body or {}
    out = privacy.pause(body.get("reason", "resident paused monitoring"),
                        body.get("by", "Margaret"))
    cancelled = db.cancel_open_checkins("monitoring_paused")
    await broadcast({"type": "privacy.paused", **out, "checkins_cancelled": cancelled})
    return out


@app.post("/api/privacy/resume")
async def privacy_resume():
    out = privacy.resume()
    await broadcast({"type": "privacy.resumed", **out})
    return out


@app.post("/api/privacy/delete-all")
async def privacy_delete_all(body: dict):
    """Irreversible, so it needs the literal word typed by a person."""
    try:
        out = privacy.delete_everything(str(body.get("confirm", "")))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await broadcast({"type": "privacy.deleted", "media": out["media"]})
    return out


@app.get("/api/privacy/today")
def privacy_today():
    return privacy.what_was_recorded_today()


@app.post("/api/maintenance/sweep")
def maintenance_sweep():
    return privacy.sweep_retention()


@app.post("/api/incidents/{incident_id}/checkin")
async def checkin(incident_id: str, request: Request):
    """Resident-facing spoken check-in endpoint. The transcript is untrusted input."""
    inc = db.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "unknown incident")
    was_pending = inc.get("checkin_status") == "asking"
    if not db.notifications_for(incident_id) and not was_pending:
        raise HTTPException(409, "no alert has been sent for this incident")

    raw = await request.body()
    content_type = request.headers.get("content-type", "")
    is_text = "application/json" in content_type
    audio_bytes = 0

    if is_text:
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "bad answer JSON") from exc
        transcript = str((payload or {}).get("answer") or "").strip()[:500]
    else:
        if not raw:
            raise HTTPException(422, "expected a wav body or a JSON answer")
        work = MEDIA_ROOT / incident_id
        work.mkdir(parents=True, exist_ok=True)
        wav = work / "checkin.wav"
        wav.write_bytes(raw)
        normalized = work / "checkin_16k.wav"
        audio_bytes = len(raw)
        try:
            # Browsers already resample to 16 kHz, but a phone client or a local
            # voice note may not. Normalise on the server so ASR is not coupled to
            # the device that recorded the answer.
            source = await asyncio.to_thread(audio_util.to_wav, wav, normalized)
            transcript = await asyncio.to_thread(meta().transcribe, source)
        except MetaError as exc:
            log.error("checkin %s: transcribe failed: %s", incident_id, exc)
            raise HTTPException(503, "transcription unavailable") from exc
        except (ValueError, OSError) as exc:
            log.error("checkin %s: could not decode answer: %s", incident_id, exc)
            raise HTTPException(422, "could not decode the answer audio") from exc
        finally:
            # Audio is deleted after transcription unless a family opts into
            # keeping it. Nothing here opts in.
            wav.unlink(missing_ok=True)
            normalized.unlink(missing_ok=True)

    sensors = inc.get("sensors") or {}
    track = inc.get("track") or {}
    audio = sensors.get("audio") or {}
    media = sensors.get("media") or {}
    sam = inc.get("sam") or {}
    posture = str(track.get("posture") or sensors.get("posture") or "").lower()
    person_count = int(track.get("person_count") or sensors.get("person_count") or 0)
    if sensors.get("person_present") is True:
        person_count = max(1, person_count)
    no_recovery = float(sensors.get("no_recovery_s") or 0)
    recovered = bool(sensors.get("recovered"))
    on_floor = (posture in ("lying", "fallen", "on_floor")
                or bool(sensors.get("on_floor"))
                or (inc.get("type") == "fall_confirmed" and not recovered)
                or (no_recovery >= 8 and not recovered))
    still_s = float(sensors.get("still_s") or no_recovery or 0)
    if str(inc.get("type", "")).startswith("fall"):
        upright_moving = (not on_floor and not sam.get("track_unavailable")
                          and recovered)
    else:
        upright_moving = (not on_floor and person_count > 0
                          and posture not in ("lying", "fallen", "on_floor"))

    classifier_class = policy.classify_transcript(transcript)
    read = None
    model_used = "rules"
    read_latency = 0
    # Muse reads the answer. The deterministic classifier is the floor: the
    # model may surface a cry the word list missed, but it can never turn a
    # "help" into "affirmative". That is the same invariant as the alert path.
    if transcript.strip():
        context = {
            "incident": {"type": inc.get("type"), "zone": inc.get("zone_id"),
                         "question": inc.get("checkin_prompt"),
                         "started_at_local": inc.get("local_time")},
            "resident": {"display_name": PROFILE.display_name},
            "transcript": transcript,
            "classifier_class": classifier_class,
            "perception": {
                "posture": posture or "unknown",
                "person_count": person_count,
                "on_floor": on_floor,
                "still_s": still_s,
                "no_recovery_s": no_recovery,
                "recovered": recovered,
                "track_available": not bool(sam.get("track_unavailable")),
                "objects": sensors.get("objects") or [],
                "support_surface": sensors.get("support_surface"),
            },
            "audio": audio,
            "media": media,
        }
        started = time.time()
        try:
            raw, usage = await asyncio.to_thread(
                meta().decide, context, CHECKIN_READ_PROMPT, CHECKIN_READ_SCHEMA, "low",
            )
            read_latency = int((time.time() - started) * 1000)
            model_class = str(raw.get("class") or "other")
            if model_class not in policy.CHECKIN_CLASSES:
                model_class = "other"
            model_read = str(raw.get("read") or "").strip()[:160]
            model_confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.5)))
            read = {
                "class": model_class,
                "read": model_read,
                "evidence": list(raw.get("evidence") or [])[:6],
                "confidence": model_confidence,
                "model": SPARK_MODEL,
                "latency_ms": read_latency,
            }
            db.add_outbound(incident_id, "api.meta.ai", "checkin_interpret",
                            len(json.dumps(context)), usage.output_tokens, "30d", False,
                            latency_ms=usage.latency_ms, model=SPARK_MODEL)
            model_used = SPARK_MODEL
        except (MetaError, ValueError, KeyError) as exc:
            log.warning("checkin read failed for %s (%s); using rules", incident_id, exc)

    # Safety merge: model may raise, never lower.
    cls = classifier_class
    if read:
        if classifier_class == "help" or read["class"] == "help":
            cls = "help"
        elif classifier_class == "affirmative" and read["class"] in ("confused", "other"):
            cls = read["class"]
        elif classifier_class in ("confused", "other") and read["class"] == "affirmative":
            cls = classifier_class
    verdict = policy.checkin_outcome(cls, upright_moving=upright_moving,
                                     on_floor=on_floor, still_s=still_s)

    decision = (inc.get("decision") or {})
    floor_now, _ = effective_floor(inc)
    current = policy.max_severity(
        decision.get("severity") or inc.get("severity") or "info", floor_now.severity)
    floor = policy.max_severity(decision.get("policy_floor") or "info", floor_now.severity)
    delta = verdict["severity_delta"]
    if delta:
        order = list(policy.ORDER)
        idx = max(0, min(len(order) - 1, order.index(current) + delta))
        # The floor still wins. A transcript cannot take an alert below it.
        new = policy.max_severity(order[idx], floor)
    else:
        new = current

    # If this was a pending spoken check-in, the answer ends its deadline.
    if was_pending:
        db.mark_checkin_answered(incident_id)
    db.add_checkin(f"chk_{incident_id[-8:]}_{int(time.time())}", incident_id, transcript,
                   verdict["resolution"], model_used, read)
    # The audio itself went to the model, and the ledger must say so. A row
    # that reports only a byte count would let "what left your home" answer
    # "some data" when the honest answer is "ten seconds of her voice".
    if audio_bytes:
        db.add_outbound(incident_id, "api.meta.ai", "transcribe", audio_bytes, 0, "1h", False,
                        latency_ms=0, model=TRANSCRIBE_MODEL, contains_audio=True)

    escalated = delta > 0 and new != current
    if escalated:
        name = (PROFILE.display_name or "the resident").split()[0]
        title = f"{name} asked for help" if cls == "help" else f"Check-in: {name}"
        if read and read.get("read"):
            body = f"{read['read']} Please call her now."
        else:
            body = f"Her answer: “{transcript[:140]}”. {verdict['reason']}."
        actions = ([{"id": "call_resident", "label": "Call now"},
                    {"id": "send_help", "label": "Send help"},
                    {"id": "im_checking", "label": "I'm checking"}]
                   if cls == "help" else
                   [{"id": "call_resident", "label": "Call now"},
                    {"id": "im_checking", "label": "I'm checking"},
                    {"id": "false_alarm", "label": "False alarm"}])
        checkin_decision = policy.Decision(
            severity=new, notify_now=True, title=title[:60], body=body[:240],
            evidence=[], actions=actions, confidence=0.9, follow_up_s=0,
            escalate_if_no_ack=False,
            rationale=("The resident answered the check-in; the safety merge "
                       "raised the incident one step."),
        )
        db.add_decision(incident_id, 3, floor, list(decision.get("floor_reasons") or []),
                        checkin_decision, model_used, PROMPT_VERSION, [],
                        read_latency if model_used == SPARK_MODEL else 0)
        db.set_status(incident_id, "notified", new)
        if not inc.get("acked_at"):
            await notify.dispatch({"incident_id": incident_id, "title": title,
                                   "body": body[:240], "severity": new}, 3, broadcast)
            db.mark_phase(incident_id, 3)
    elif delta < 0:
        db.set_status(incident_id, "resolved", new)

    await broadcast({"type": "checkin.answered", "id": incident_id,
                     "transcript": transcript, "class": cls,
                     "classifier_class": classifier_class,
                     "severity": new, "previous": current,
                     "contradiction": verdict["contradiction"],
                     "reason": verdict["reason"], "read": read,
                     "model": model_used})
    log.info("checkin %s: %s/%s -> %s (%s) model=%s", incident_id, classifier_class,
             cls, new, verdict["reason"], model_used)
    return {"transcript": transcript, "class": cls,
            "classifier_class": classifier_class, "severity": new,
            "previous": current, "contradiction": verdict["contradiction"],
            "resolution": verdict["resolution"], "reason": verdict["reason"],
            "read": read, "model": model_used}


class SimIn(BaseModel):
    scenario: str = "door"
    # Force an hour so a demo recorded at 3 PM can show the 3 AM rule. Without
    # this the control room can only ever demonstrate daytime behaviour, which
    # is the half of the product that does not matter.
    hour: int | None = None
    # The control room can exercise the spoken check-in too. The deadline is
    # real; an unanswered demo incident will escalate on its own after 15s.
    checkin: bool = True


_sim_hits: dict[str, list[float]] = {}


def _sim_budget(who: str, limit: int = 10, window: float = 60.0) -> tuple[bool, int]:
    """Ten scenarios a minute per caller, per §8.7."""
    now = time.time()
    hits = [t for t in _sim_hits.get(who, []) if now - t < window]
    if len(hits) >= limit:
        return False, int(window - (now - hits[0])) + 1
    hits.append(now)
    _sim_hits[who] = hits
    return True, 0


@app.post("/api/sim/trigger")
async def sim_trigger(payload: SimIn, background: BackgroundTasks,
                      request: Request = None):
    """Control room: fire a scripted scenario through the real pipeline.

    Deliberately not a shortcut around the pipeline. It builds the same
    incident packet the edge agent posts, so the floor, the SAM pass, the model
    decision and the ledger all run exactly as they would at 3 AM.
    """
    # Ten a minute. The control room is behind a PIN in a real deployment, and
    # a demo that can be scripted into a hundred model calls is a demo that
    # spends the family's API budget.
    who = auth.client_of(request) if request else "local"
    allowed, wait = _sim_budget(who)
    if not allowed:
        raise HTTPException(429, f"too many scenarios, try again in {wait}s")
    now = datetime.now(TZ)
    hour = payload.hour if payload.hour is not None and 0 <= payload.hour <= 23 else now.hour
    # A night demo fired at 3 PM should still demonstrate the night rule; the
    # control room can override this with an explicit hour.
    if payload.hour is None and payload.scenario in ("night_visitor", "night_visitor_expected"):
        # Put the demo inside the night window the Night page is showing now,
        # without placing it in the future. Outside the dark window, the
        # previous night's 03:00 is the honest demo slot.
        local_hour = now_local().hour
        if local_hour >= 22 or local_hour < 6:
            hour = local_hour
        else:
            hour = 3
    elif payload.hour is None and payload.scenario == "night_visitor_day":
        hour = 14
    # The incident clock, not the wall clock. A demo recorded in the afternoon
    # must be able to show the 3 AM door rule, and the floor reads the incident's
    # own hour so that stays honest rather than being faked downstream.
    stamp = now.replace(hour=hour, minute=now.minute, second=now.second)
    night = hour >= 22 or hour < 6
    scenarios = {
        "door": ("door_open", "front_door", {"door_contact": "open"}),
        "door_left_open": ("door_left_open", "front_door",
                           {"door_contact": "open", "door_open_s": 240}),
        "fall": ("fall_confirmed", "hallway", {"no_recovery_s": 12}),
        "fall_recovered": ("fall_confirmed", "hallway",
                           {"no_recovery_s": 4, "recovered": True}),
        "fall_assisted": ("fall_assisted", "hallway",
                          {"helper_present": True, "no_recovery_s": 6}),
        "stove": ("stove_unattended", "kitchen",
                  {"stove_power_w": 1240, "absent_s": 420}),
        "stove_long": ("stove_unattended", "kitchen",
                       {"stove_power_w": 1240, "absent_s": 700, "smoke_visible": True}),
        "pet": ("pet_motion", "hallway", {"person_check": False}),
        "prayer": ("fall_suspected", "prayer_room",
                   {"posture_transitions": 8, "recurring_daily": True}),
        "caregiver": ("door_open", "front_door",
                      {"door_contact": "open", "expected_visitor": True}),
        "still": ("person_unwell_still", "sofa", {"still_min": 780}),
        "camera_offline": ("camera_offline", "hallway", {"camera_fps": 0}),
        # Audio-distress scenarios. The transcript and the TV/radio state are
        # what an edge audio detector plus a media sensor would send; this is a
        # replay of that context, not a microphone listening in the room.
        "help_call": ("help_call", "hallway", {
            "person_present": True, "person_count": 1, "posture": "seated",
            "media": {"tv_on": False, "radio_on": False, "verified": True},
            "audio": {"distress": True, "kind": "help_words",
                      "transcript": "Help me, please. I can't get up."},
        }),
        "help_call_tv": ("help_call", "hallway", {
            "person_present": True, "person_count": 1, "posture": "upright",
            "media": {"tv_on": True, "radio_on": False, "verified": True},
            "audio": {"distress": True, "kind": "help_words",
                      "transcript": "Help me, please."},
        }),
        "help_call_floor": ("help_call", "hallway", {
            "person_present": True, "person_count": 1, "posture": "lying",
            "no_recovery_s": 12,
            "media": {"tv_on": False, "radio_on": False, "verified": True},
            "audio": {"distress": True, "kind": "help_words",
                      "transcript": "Help, I fell and can't get up."},
        }),
        "help_call_tv_only": ("help_call", "living_room", {
            "person_present": False, "person_count": 0, "posture": "unknown",
            "media": {"tv_on": True, "radio_on": False, "verified": True},
            "audio": {"distress": True, "kind": "scream_like", "transcript": ""},
        }),
        "night_visitor": ("unexpected_presence", "front_door", {
            "person_count": 2, "second_person_present": True, "posture": "upright",
            "expected_visitor": False, "door_contact": "open", "resident_present": True,
        }),
        "night_visitor_expected": ("unexpected_presence", "front_door", {
            "person_count": 2, "second_person_present": True, "posture": "upright",
            "expected_visitor": True, "door_contact": "open", "resident_present": True,
        }),
        "night_visitor_day": ("unexpected_presence", "front_door", {
            "person_count": 2, "second_person_present": True, "posture": "upright",
            "expected_visitor": False, "door_contact": "open", "resident_present": True,
        }),
        "restlessness": ("restlessness", "hallway", {
            "path_length": 1.8, "direction_changes": 9, "present_s": 42,
            "person_count": 1, "posture": "upright", "night": False,
        }),
        "restlessness_night": ("restlessness", "hallway", {
            "path_length": 2.4, "direction_changes": 14, "present_s": 55,
            "person_count": 1, "posture": "upright", "night": True,
        }),
    }
    if payload.scenario not in scenarios:
        raise HTTPException(422, f"unknown scenario; expected one of {sorted(scenarios)}")
    itype, zone, sensors = scenarios[payload.scenario]
    iid = f"sim_{payload.scenario}_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    person_count = int(sensors.get("person_count", 1))
    if sensors.get("person_present") is False:
        person_count = 0
    media = sensors.get("media") or {}
    audio = sensors.get("audio") or {}
    evidence = [f"type:{itype}", f"zone:{zone}"]
    if audio:
        evidence += ["call_for_help", f"audio_{audio.get('kind', 'distress')}"]
    if media.get("tv_on"):
        evidence.append("media_tv_on")
    if media.get("radio_on"):
        evidence.append("media_radio_on")
    if itype == "unexpected_presence":
        evidence.append("second_person_present")
        if night:
            evidence.append("night_hours")
        if sensors.get("door_contact") == "open":
            evidence.append("door_contact_open")
    packet = {
        "incident_id": iid, "household_id": HOUSEHOLD, "camera_id": "cam_hallway",
        "type": itype, "status": "detected", "started_at": stamp.astimezone(timezone.utc).isoformat(),
        "local_time": stamp.isoformat(), "zone_id": zone,
        "track": {"primary_track_id": 0,
                  "track_ids": list(range(person_count)),
                  "person_count": person_count,
                  "posture": sensors.get("posture", "upright"),
                  "path_length": 0.42, "dwell_in_door_zone_s": 6.8,
                  "crossed_door_line": itype == "door_open",
                  "absent_s": sensors.get("absent_s", 0)},
        "sensors": {**sensors, "night": night}, "privacy": {"masked": []},
        "artifacts": {}, "evidence": evidence,
        "source": "sim",
    }
    packet = with_expected_visitor(packet)
    db.upsert_incident(packet)
    floor, plan_note = effective_floor(packet, hour)
    db.raise_incident_severity(iid, floor.severity)
    db.record_event(iid, "cam_hallway", itype, zone, stamp.isoformat(), floor.severity,
                    suppressed=not floor.notifies, reason=",".join(floor.reasons),
                    source="sim")
    await broadcast({"type": "incident.created", "id": iid, "severity": floor.severity,
                     "floor": floor.severity, "sim": payload.scenario})
    asyncio.create_task(phase_one(iid))
    checkin_started = False
    if payload.checkin and policy.should_checkin(packet):
        asyncio.create_task(start_checkin(iid))
        checkin_started = True
    asyncio.create_task(read_ambiguous_context(iid))
    log.info("sim %s -> %s (floor %s%s, checkin=%s)", payload.scenario, iid,
             floor.severity, f", {plan_note}" if plan_note else "", checkin_started)
    return {"ok": True, "id": iid, "scenario": payload.scenario, "floor": floor.severity,
            "plan": plan_note, "checkin": checkin_started}


@app.get("/api/checkins")
def checkins():
    return {"items": db.checkins()}


@app.get("/media/{incident_id}/{name}")
def media(incident_id: str, name: str, request: Request):
    if name not in ("clip.mp4", "still.jpg", "thumb.jpg", "sam_input.mp4",
                    "annotated.mp4", "redacted.mp4", "checkin_prompt.wav"):
        raise HTTPException(404, "not found")
    # A clip is the most private object in the system. If a PIN is configured,
    # a session cookie or a five-minute signature is required — and the answer
    # is a plain 401, never a redirect that leaks the path.
    if not auth.media_ok(incident_id, name, request):
        raise HTTPException(401, "sign in to view this clip")
    path = MEDIA_ROOT / incident_id / name
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})


# --------------------------------------------------------------------------
# continuous multi-camera wall
# --------------------------------------------------------------------------
def _build_wall_clip(incident_id: str,
                     frames: list[tuple[float, bytes]]) -> tuple[Path, Path, Path] | None:
    """Encode the in-memory alert buffer into a short clip for SAM and the family.

    This is the wall's equivalence of the edge's ring buffer. It runs only when
    a local rule has already raised an alert; the buffer is otherwise never
    written to disk. Four frames per second is enough for SAM's masks and for a
    person to see what happened.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    decoded: list[tuple[float, "np.ndarray"]] = []
    for ts, jpeg in frames:
        arr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            continue
        h, w = arr.shape[:2]
        if w > 960:
            scale = 960.0 / w
            arr = cv2.resize(arr, (960, max(2, int(round(h * scale)) // 2 * 2)),
                             interpolation=cv2.INTER_AREA)
        decoded.append((ts, arr))
    if len(decoded) < 2:
        return None
    h, w = decoded[0][1].shape[:2]
    w, h = w - (w % 2), h - (h % 2)
    duration = max(1.0, min(7.0, decoded[-1][0] - decoded[0][0]))
    fps = 4
    count = max(8, int(round(duration * fps)))
    out_dir = MEDIA_ROOT / incident_id
    out_dir.mkdir(parents=True, exist_ok=True)
    clip = out_dir / "clip.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        return None
    index = 0
    try:
        for i in range(count):
            target = decoded[0][0] + duration * i / max(1, count - 1)
            while index + 1 < len(decoded) and decoded[index + 1][0] <= target:
                index += 1
            frame = decoded[index][1]
            if frame.shape[1] != w or frame.shape[0] != h:
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            writer.write(frame)
    finally:
        writer.release()
    if not clip.exists() or clip.stat().st_size < 1024:
        return None
    still = out_dir / "still.jpg"
    thumb = out_dir / "thumb.jpg"
    cv2.imwrite(str(still), decoded[-1][1])
    th, tw = decoded[-1][1].shape[:2]
    cv2.imwrite(str(thumb), cv2.resize(
        decoded[-1][1], (480, max(2, int(round(th * 480 / tw)) // 2 * 2)),
        interpolation=cv2.INTER_AREA))
    return clip, still, thumb


def _wall_incident_packet(session: str, feed: dict, alert: dict) -> dict:
    """Turn one sustained wall finding into the same incident an edge would send."""
    now = now_local()
    evidence = alert.get("evidence") or {}
    count = int(evidence.get("person_count") or 0)
    lying_for = float(evidence.get("lying_for_s") or 0)
    objects = evidence.get("objects") or []
    return {
        "incident_id": f"inc_wall_{uuid.uuid4().hex[:12]}",
        "household_id": HOUSEHOLD,
        "camera_id": "cam_wall",
        "type": "fall_suspected",
        "status": "detected",
        "started_at": now.astimezone(timezone.utc).isoformat(),
        "local_time": now.isoformat(),
        "zone_id": None,
        "severity_hint": "urgent",
        "track": {
            "track_id": int(evidence.get("track_id") or 0),
            "primary": True,
            "person_count": count,
            "posture": "lying",
            "support_surface": None,
        },
        "sensors": {
            "posture": "lying",
            "support_surface": None,
            "person_present": count > 0,
            "person_count": count,
            "no_recovery_s": lying_for,
            "still_s": lying_for,
            "recovered": False,
            "second_person_present": count > 1,
            "expected_visitor": False,
            "night": now.hour >= 22 or now.hour < 6,
            "objects": objects,
            "camera_fps": 0,
            "wall_feed": {
                "session": session,
                "feed_id": str(feed.get("id") or "")[:40],
                "feed_name": str(feed.get("name") or "")[:60],
                "kind": str(feed.get("kind") or "file")[:20],
            },
        },
        "privacy": {"masks_applied": [], "audio": False, "raw_video_left_home": False},
        "artifacts": {},
        "evidence": ["wall_feed", "posture_lying",
                     f"lying_{round(lying_for, 1)}s", "no_recovery"],
        "source": "wall",
    }


async def _create_wall_incident(
    packet: dict,
    clip: tuple[Path, Path, Path] | None = None,
) -> str:
    """Insert a provisional incident that no family can see yet.

    Status `verifying` is deliberately not in the worker's queue. The row exists
    so the evidence clip, SAM features and ledger have a stable id; phase 1 and
    the check-in are released only after SAM and Muse corroborate the finding.
    """
    packet = {**packet, "status": "verifying"}
    db.upsert_incident(packet)
    if clip is not None:
        clip_path, still_path, thumb_path = clip
        try:
            db.attach_clip(packet["incident_id"], str(clip_path), str(still_path),
                           str(thumb_path), clip_path.stat().st_size)
        except OSError as exc:
            log.warning("wall clip attach failed for %s: %s", packet["incident_id"], exc)
    floor, _ = effective_floor(packet)
    db.raise_incident_severity(packet["incident_id"], floor.severity)
    db.record_event(packet["incident_id"], packet["camera_id"], packet["type"],
                    packet.get("zone_id"), packet.get("local_time") or db.now(),
                    floor.severity, suppressed=True, reason="verification_pending",
                    source="wall")
    return packet["incident_id"]


async def _release_wall_incident(incident_id: str, verification: str,
                                decision: policy.Decision | None = None,
                                trace: list[dict] | None = None,
                                violations: list[str] | None = None,
                                latency: int = 0) -> None:
    """Verification passed (or failed open): this is the first family contact.

    When the evidence agent produced a decision, that verified decision is the
    family alert. There is no template phase 1 followed by a second model pass:
    the agent already did the investigation before this point.
    """
    incident = db.get_incident(incident_id)
    if not incident:
        return
    if decision is not None:
        decision = policy.ensure_fall_copy(decision, incident.get("type", ""), PROFILE)
    db.update_incident_sensors(incident_id, {"verification": verification})
    if trace:
        db.attach_agent(incident_id, trace)
    await broadcast({"type": "incident.created", "id": incident_id,
                     "incident_type": incident["type"],
                     "floor": incident.get("severity"), "wall": True})
    if decision is not None:
        floor, _ = effective_floor(incident)
        stored = db.add_decision(incident_id, 2, floor.severity, floor.reasons,
                                 decision, SPARK_MODEL, PROMPT_VERSION,
                                 violations or [], latency)
        db.set_status(incident_id, "notified", stored)
        if decision.notify_now or floor.notifies:
            await notify.dispatch({
                "incident_id": incident_id,
                "title": decision.title,
                "body": decision.body,
                "severity": stored,
            }, 2, broadcast)
            due = (time.time() + decision.follow_up_s) if decision.escalate_if_no_ack else None
            db.mark_phase(incident_id, 2, due)
        await broadcast({"type": "incident.decided", "id": incident_id,
                         "severity": stored, "title": decision.title,
                         "wall": True, "verified": True})
        asyncio.create_task(start_checkin(incident_id))
        log.info("wall incident %s released with verified decision (%s)", incident_id, verification)
        return
    # Verification unavailable: fall back to the deterministic floor/template.
    try:
        await phase_one(incident_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("wall phase1 failed for %s: %s", incident_id, exc)
    db.set_status(incident_id, "detected")
    asyncio.create_task(start_checkin(incident_id))
    log.info("wall incident %s released without verification (%s)", incident_id, verification)


async def _reject_wall_incident(incident_id: str, reason: str) -> None:
    """Verification failed: keep the audit row, send no family alert."""
    incident = db.get_incident(incident_id)
    if not incident:
        return
    db.set_status(incident_id, "resolved")
    db.record_event(incident_id, incident.get("camera_id"), incident.get("type"),
                    incident.get("zone_id"), db.now(), "info", suppressed=True,
                    reason=f"verification_rejected:{reason}"[:120], source="wall")
    log.info("wall incident %s rejected before escalation: %s", incident_id, reason[:120])


async def _collect_wall_evidence(session: str, feed_id: str,
                                 target_s: float = 5.0,
                                 max_wait_s: float = 9.0) -> list[tuple[float, bytes]]:
    """Let the continuous buffer reach a 5–7 second window before the clip is cut.

    In normal operation the buffer already holds the pre-fall seconds. On the
    first alert after a cold start it may not, so this waits for the live feed
    to fill the window rather than sending a one-second fragment to SAM.
    """
    started = time.time()
    frames: list[tuple[float, bytes]] = []
    while time.time() - started < max_wait_s:
        frames = wall.evidence_frames(session, feed_id, seconds=7.5)
        if frames and frames[-1][0] - frames[0][0] >= target_s:
            break
        await asyncio.sleep(0.4)
    return wall.evidence_frames(session, feed_id, seconds=7.5)


def _agent_tools(sensors: dict) -> dict:
    async def memory_tool():
        return {"facts": [m["fact"] for m in db.memory(HOUSEHOLD)]}

    async def events_tool():
        return {"items": db.recent_events(12)}

    async def audio_tool():
        audio = (sensors or {}).get("audio") or {}
        media = (sensors or {}).get("media") or {}
        if not audio and not media:
            return {"available": False}
        return {"available": True, "audio": audio, "media": media}

    return {
        "household_memory": memory_tool,
        "recent_events": events_tool,
        "audio_context": audio_tool,
    }


def _agent_logger(incident_id: str):
    async def log_call(purpose: str, nbytes: int, usage) -> None:
        db.add_outbound(incident_id, "api.meta.ai", purpose, nbytes,
                        usage.output_tokens, "30d", False,
                        latency_ms=usage.latency_ms, model=SPARK_MODEL)
    return log_call


async def _verify_wall_alert(session: str, feed_id: str, alert_id: str,
                             incident_id: str, packet: dict,
                             clip: tuple[Path, Path, Path] | None = None) -> None:
    """SAM + Muse gate. A wall fall reaches the family only after this passes.

    Verification failure is fail-open for a possible fall: the local floor is
    already urgent, and dropping a real fall because a cloud model was slow is
    worse than a false alert. The wall says which path happened.
    """
    async def publish(status: str, **data) -> None:
        alert = wall.set_verification(session, feed_id, alert_id, status=status, **data)
        if alert is not None:
            await broadcast({"type": "wall.verification", "session": session,
                             "feed_id": feed_id, "alert": alert})

    try:
        await publish("verifying",
                      reason="Collecting a 5–7 second evidence clip, then checking it with SAM and Muse. No family alert has been sent.",
                      escalated=False)
        if clip is None:
            frames = await _collect_wall_evidence(session, feed_id)
            clip = await asyncio.to_thread(
                _build_wall_clip, incident_id, frames)
            if clip is not None:
                clip_path, still_path, thumb_path = clip
                db.attach_clip(incident_id, str(clip_path), str(still_path),
                               str(thumb_path), clip_path.stat().st_size)
        if clip is None:
            raise MetaError("no evidence clip")
        clip_seconds = await asyncio.to_thread(clip_util.duration_s, clip[0])
        live = wall.latest_alert(session, feed_id) or {}
        live_evidence = live.get("evidence") or {}
        # The trigger may have opened at one second; by the time the clip is
        # collected the person may have been down much longer. Give the verifier
        # the live duration, not the trigger instant.
        observed_lying = max(float(live_evidence.get("lying_for_s") or 0),
                             float((packet.get("sensors") or {}).get("no_recovery_s") or 0))
        await publish("verifying",
                      reason="Evidence clip collected. SAM is tracking it; Muse is gathering household evidence before deciding.",
                      clip_seconds=round(float(clip_seconds or 0), 1),
                      escalated=False)
        features = await asyncio.wait_for(
            _sam_features_for_clip(incident_id, clip[0], packet.get("track") or {}),
            timeout=40.0,
        )
        sam_count = int(features.get("person_count") or 0)
        sam_seen = bool(features.get("frames_present") or sam_count)
        sensors = packet.get("sensors") or {}
        floor, _ = effective_floor(packet)
        trace: list[dict] = []
        await publish("verifying",
                      reason="SAM tracked the clip. Muse is gathering household evidence before deciding whether any family alert is allowed.",
                      clip_seconds=round(float(clip_seconds or 0), 1),
                      sam_person_count=sam_count, sam_seen=sam_seen,
                      escalated=False)
        verify_packet = {
            "incident": {"type": "fall_suspected", "source": "wall", "zone": None},
            "feed": (sensors.get("wall_feed") or {}),
            "local": {
                "kind": "possible_fall",
                "posture": live_evidence.get("posture") or sensors.get("posture"),
                "support_surface": live_evidence.get("support_surface",
                                                     sensors.get("support_surface")),
                "lying_for_s": observed_lying,
                "person_count": live_evidence.get("person_count") or sensors.get("person_count"),
                "objects": live_evidence.get("objects") or sensors.get("objects") or [],
            },
            "sam": {
                "frames_present": features.get("frames_present"),
                "person_count": sam_count,
                "track_unavailable": bool(features.get("track_unavailable")),
                "error": features.get("error"),
            },
            "memory": [m["fact"] for m in db.memory(HOUSEHOLD)][:5],
            "history": db.recent_events(8),
            "policy": {"floor": floor.severity, "floor_reasons": floor.reasons},
            "constraints": {"clip_seconds": round(float(clip_seconds or 0), 1),
                            "no_pixels": True,
                            "do_not_claim": ["identity", "intent", "medical", "safe"]},
        }
        agent = EvidenceAgent(meta())
        result = await asyncio.wait_for(
            agent.run(
                packet=verify_packet,
                tools=_agent_tools(sensors),
                verify=True,
                floor=floor.severity,
                log_call=_agent_logger(incident_id),
                effort="low",
            ),
            timeout=75.0,
        )
        trace = result.trace
        db.attach_agent(incident_id, trace)
        raw = result.raw
        reason = str(raw.get("rationale") or raw.get("title") or "")[:180]
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        if raw.get("verdict") == "escalate":
            decision = policy.Decision(
                severity=str(raw.get("severity") or floor.severity),
                notify_now=bool(raw.get("notify_now", True)),
                title=str(raw.get("title") or "Possible fall")[:120],
                body=str(raw.get("body") or "")[:400],
                evidence=list(raw.get("evidence") or [])[:8],
                actions=list(raw.get("actions") or []),
                confidence=confidence,
                follow_up_s=int(raw.get("follow_up_s") or 60),
                escalate_if_no_ack=bool(raw.get("escalate_if_no_ack", True)),
                rationale=str(raw.get("rationale") or "")[:300],
            )
            gate = policy.EvidenceGate(result.final_packet,
                                       extra=set(packet.get("evidence") or []))
            decision, violations = policy.validate(decision, floor, gate)
            await _release_wall_incident(
                incident_id, "sam_muse_verified", decision=decision,
                trace=trace, violations=violations, latency=result.latency_ms)
            await publish("verified",
                          reason=reason or "SAM and household evidence support the finding.",
                          confidence=confidence, sam_person_count=sam_count,
                          sam_seen=True, clip_seconds=round(float(clip_seconds or 0), 1),
                          severity=decision.severity, trace=trace,
                          escalated=True)
        else:
            await _reject_wall_incident(incident_id, reason or "agent rejected the candidate")
            await publish("rejected",
                          reason=reason or "The evidence did not support the local finding.",
                          confidence=confidence, sam_person_count=sam_count,
                          sam_seen=sam_seen, clip_seconds=round(float(clip_seconds or 0), 1),
                          trace=trace, escalated=False)
    except Exception as exc:  # noqa: BLE001
        reason = f"Verification unavailable: {type(exc).__name__}: {str(exc)[:120]}"
        await _release_wall_incident(incident_id, "verification_unavailable_local_floor")
        await publish("unavailable", reason=reason, escalated=True,
                      trace=trace if "trace" in locals() else [])
        log.warning("wall verification unavailable for %s; escalated on local floor: %s",
                    incident_id, exc)


@app.post("/api/wall/frame")
async def wall_frame(session: str = Form(...), feed: str = Form(...),
                     frame: UploadFile = File(...)):
    """Analyze one wall frame. Sustained falls become held incidents."""
    session = session[:80].strip()
    if not session:
        raise HTTPException(422, "session is required")
    try:
        feed_meta = json.loads(feed)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "feed must be JSON") from exc
    if not isinstance(feed_meta, dict) or not str(feed_meta.get("id") or ""):
        raise HTTPException(422, "feed id is required")
    body = await frame.read()
    if not body:
        raise HTTPException(422, "frame is empty")
    if len(body) > 5 * 1024 * 1024:
        raise HTTPException(422, "frame exceeds 5 MB")
    try:
        result = await asyncio.to_thread(wall.process, session, feed_meta, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("wall frame failed: %s", exc)
        raise HTTPException(503, f"local scene detection unavailable: {exc}") from exc

    trigger = result.pop("trigger", None)
    if trigger is not None and trigger.get("kind") == "possible_fall":
        packet = _wall_incident_packet(session, feed_meta, trigger)
        try:
            incident_id = await _create_wall_incident(packet, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("wall verification incident failed: %s", exc)
            result["escalation_error"] = "the local finding stands, but its evidence record could not be created"
        else:
            wall.link_incident(session, result["feed_id"], trigger["id"], incident_id)
            pending = wall.set_verification(
                session, result["feed_id"], trigger["id"],
                status="pending", escalated=False,
                reason="Collecting a 5–7 second evidence clip. SAM and Muse verify it before any family alert.",
            )
            result["alert"]["incident_id"] = incident_id
            if pending is not None:
                result["alert"]["verification"] = pending["verification"]
            result["incident"] = {"id": incident_id, "verification": "pending",
                                  "clip": "collecting"}
            asyncio.create_task(_verify_wall_alert(
                session, result["feed_id"], trigger["id"], incident_id, packet))

    alert = result.get("alert")
    resolved = result.get("resolved")
    if alert and alert.get("incident_id"):
        db.update_incident_sensors(alert["incident_id"], {
            "no_recovery_s": result["tracking"]["lying_for_s"],
            "still_s": result["tracking"]["lying_for_s"],
            "posture": "lying" if result["tracking"]["lying_for_s"] > 0 else "upright",
            "recovered": result["tracking"]["recovered"],
            "person_count": result["person_count"],
        })
    elif resolved and resolved.get("incident_id"):
        db.update_incident_sensors(resolved["incident_id"], {
            "recovered": bool(resolved.get("recovered")),
            "still_s": 0,
        })
    return result


class WallCloseIn(BaseModel):
    session: str


@app.post("/api/wall/session/close")
def wall_close(body: WallCloseIn):
    wall.close(body.session[:80])
    return {"ok": True}


# --------------------------------------------------------------------------
# SSE
# --------------------------------------------------------------------------
@app.get("/api/events/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _subscribers.add(q)

    async def gen():
        try:
            yield f"event: snapshot\ndata: {json.dumps({'items': db.list_incidents(20)})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {item.get('type', 'message')}\ndata: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def broadcast(event: dict) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


# --------------------------------------------------------------------------
# phase 1: the fast alert
# --------------------------------------------------------------------------
async def phase_one(incident_id: str) -> None:
    """Notify within a second of ingest, from the floor and a template.

    This is the path that saves the minutes. It knows nothing about the clip and
    that is the point: it cannot be blocked by a slow upload or a slow model.
    """
    incident = db.get_incident(incident_id)
    if not incident:
        return
    local_hour = incident_hour(incident)
    floor, plan_note = effective_floor(incident, local_hour)
    decision = policy.template_decision(floor, incident, PROFILE,
                                        incident_clock(incident))
    decision.severity = policy.max_severity(decision.severity, floor.severity)

    if not decision.notify_now and not floor.notifies:
        db.mark_phase(incident_id, 1)
        log.info("phase1 %s stayed home (floor %s%s)", incident_id, floor.severity,
                 f", {plan_note}" if plan_note else "")
        return

    # Alert fatigue is a safety bug: the seventh buzz of the hour is how a real
    # fall gets ignored. A repeat inside the window updates the existing alert
    # instead of starting a second conversation about the same thing.
    previous = db.previous_event(incident_id)
    action = policy.dedupe_action(previous, {**incident, "age_s": previous.get("age_s", 0)
                                             if previous else 0})
    if action == "suppress":
        db.mark_phase(incident_id, 1)
        log.info("phase1 %s suppressed (repeat of %s within cooldown)",
                 incident_id, previous.get("incident_id") if previous else "?")
        await broadcast({"type": "incident.suppressed", "id": incident_id,
                         "reason": "false_alarm_cooldown"})
        return

    alert = {"incident_id": incident_id, "title": decision.title,
             "body": decision.body, "severity": decision.severity,
             "dedupe": action}
    results = await notify.dispatch(alert, 1, broadcast)
    due = time.time() + decision.follow_up_s
    db.mark_phase(incident_id, 1, due if decision.escalate_if_no_ack else None)
    log.info("phase1 %s -> %s (%s) channels=%s", incident_id, decision.severity,
             action, [r["channel"] for r in results if r["ok"]])


# --------------------------------------------------------------------------
# phase 2: the enriched alert
# --------------------------------------------------------------------------
async def _sam_features_for_clip(incident_id: str, clip: str | Path,
                                 track: dict | None = None) -> dict:
    """Run SAM on one short clip and persist the result.

    Used by the wall's pre-escalation verification and by the ordinary phase-2
    path. Keeping it in one place is what makes the wall's verification
    recoverable: the worker reuses these features instead of uploading the same
    clip to SAM a second time.
    """
    clip = Path(clip)
    small = await asyncio.to_thread(
        clip_util.sam_ready, clip, MEDIA_ROOT / incident_id / "sam_input.mp4",
    )
    text, sam_usage, nbytes = await asyncio.to_thread(
        meta().sam_video, small or clip, "person"
    )
    objects = parse_sam(text)
    features = track_features(objects)
    features["edge"] = track or {}
    if objects:
        manifest = await asyncio.to_thread(
            render.render, Path(small or clip),
            MEDIA_ROOT / incident_id / "annotated.mp4", objects,
            timestamp=datetime.now(TZ).strftime("%H:%M:%S"),
        )
        features["overlay"] = manifest
    db.add_outbound(incident_id, "api.meta.ai", "sam_track",
                    nbytes, len({o.frame for o in objects}), "1h", True,
                    latency_ms=sam_usage.latency_ms, model=SAM_MODEL)
    db.attach_sam(incident_id, features)
    return features


async def enrich(incident: dict) -> None:
    started = time.time()
    itype = incident["type"]
    local_hour = incident_hour(incident)
    floor, plan_note = effective_floor(incident, local_hour)

    features: dict = {}
    clip = incident.get("clip_path") or ""
    existing = incident.get("sam") or {}
    if clip and Path(clip).exists() and existing and not existing.get("error"):
        # The wall verified this clip before any family alert; reuse that work.
        features = {**existing, "edge": existing.get("edge") or incident.get("track") or {}}
    elif clip and Path(clip).exists():
        try:
            features = await _sam_features_for_clip(
                incident["id"], clip, incident.get("track") or {})
        except MetaError as exc:
            log.warning("SAM failed for %s: %s", incident["id"], exc)
            features = {"track_unavailable": True, "error": str(exc)[:160]}
    else:
        features = {"track_unavailable": True, "reason": "no_clip"}

    packet = build_packet(incident, features, floor, PROFILE, local_hour=local_hour,
                          history=db.recent_history(incident["id"]),
                          memory=db.memory(HOUSEHOLD),
                          quiet=policy.in_quiet_hours(local_hour))

    decision = None
    violations: list[str] = []
    model_used = "template"
    gate_packet = packet
    try:
        result = await EvidenceAgent(meta()).run(
            packet=packet,
            tools=_agent_tools(incident.get("sensors") or {}),
            verify=False,
            floor=floor.severity,
            log_call=_agent_logger(incident["id"]),
            effort="medium" if itype.startswith("fall") else "low",
        )
        raw = result.raw
        gate_packet = result.final_packet
        db.attach_agent(incident["id"], result.trace)
        decision = policy.Decision(
            severity=str(raw.get("severity", "watch")),
            notify_now=bool(raw.get("notify_now", False)),
            title=str(raw.get("title", ""))[:120],
            body=str(raw.get("body", ""))[:400],
            evidence=list(raw.get("evidence") or [])[:8],
            actions=list(raw.get("actions") or []),
            confidence=float(raw.get("confidence", 0.5)),
            follow_up_s=int(raw.get("follow_up_s", 90)),
            escalate_if_no_ack=bool(raw.get("escalate_if_no_ack", True)),
            rationale=str(raw.get("rationale", ""))[:300],
        )
        model_used = SPARK_MODEL
    except (MetaError, ValueError, KeyError, TimeoutError) as exc:
        log.warning("evidence agent failed for %s (%s); using template",
                    incident["id"], exc)
        decision = policy.template_decision(floor, incident, PROFILE,
                                            incident_clock(incident))

    # The gate is built from the packet the agent actually read (including tool
    # results), so a cited fact and a hallucinated one stay separable.
    gate = policy.EvidenceGate(gate_packet, extra=set(incident.get("evidence") or []))
    decision, violations = policy.validate(decision, floor, gate)
    decision = policy.ensure_fall_copy(decision, itype, PROFILE)

    # Quiet hours hold only `watch`; the hold is stored so the UI can say
    # "held until 07:00" instead of pretending nothing happened. The window is
    # the household's, not a constant: onboarding can move it.
    plan = current_plan()
    may_interrupt, why = policy.should_interrupt(decision, floor, local_hour,
                                                 plan["quiet"])
    deferred_until = None
    if not may_interrupt and why == "quiet_hours":
        deferred_until = time.time() + policy.defer_until(local_hour, plan["quiet"])
        violations.append("deferred_quiet_hours")
        decision.notify_now = False

    # add_decision ratchets the incident severity and returns the stored value.
    # set_status must use that value: passing the model's lower severity here is
    # how a late phase-2 write once turned an emergency back into "watch" after
    # the check-in had already timed out and paged the family.
    stored = db.add_decision(incident["id"], 2, floor.severity, floor.reasons, decision,
                             model_used, PROMPT_VERSION, violations,
                             int((time.time() - started) * 1000))
    db.set_status(incident["id"], "notified", stored)

    if (decision.notify_now or floor.notifies) and stored == decision.severity:
        alert = {"incident_id": incident["id"], "title": decision.title,
                 "body": decision.body, "severity": decision.severity}
        await notify.dispatch(alert, 2, broadcast)
        due = (time.time() + decision.follow_up_s) if decision.escalate_if_no_ack else None
        db.mark_phase(incident["id"], 2, due)
    elif stored != decision.severity:
        log.warning("phase2 %s kept %s over model %s; a higher phase already paged",
                    incident["id"], stored, decision.severity)
    log.info("phase2 %s -> %s (model %s, floor %s) in %dms %s%s", incident["id"],
             stored, decision.severity, floor.severity, int((time.time() - started) * 1000),
             violations or "", " deferred" if deferred_until else "")
    await broadcast({"type": "incident.decided", "id": incident["id"],
                     "severity": stored, "title": decision.title,
                     "model_severity": decision.severity,
                     "severity_raised": stored != floor.severity})


MAX_ATTEMPTS = 3


async def worker_loop() -> None:
    """Enrich new incidents. One poison incident must never stop the queue."""
    while True:
        try:
            for incident in db.pending_for_processing(5):
                try:
                    await enrich(incident)
                except Exception as exc:  # noqa: BLE001
                    n = db.bump_attempt(incident["id"])
                    log.exception("enrich failed %s (%d/%d): %s",
                                  incident["id"], n, MAX_ATTEMPTS, exc)
                    if n >= MAX_ATTEMPTS:
                        await fail_open(incident, exc)
                    else:
                        db.set_status(incident["id"], "enriching")
                finally:
                    db.release_lease(incident["id"])
        except Exception as exc:  # noqa: BLE001
            log.exception("worker loop error: %s", exc)
        await asyncio.sleep(1.0)


async def escalation_loop() -> None:
    """A notified alert that nobody acknowledged must not go quiet.

    This is the second half of the safety claim: phase 1 guarantees the family is
    told quickly, and this guarantees that silence is never mistaken for an
    answer.
    """
    while True:
        try:
            for inc in db.due_escalations(time.time()):
                db.mark_acked(inc["id"])          # fire once, not every tick
                await broadcast({"type": "incident.escalated", "id": inc["id"],
                                 "reason": "no_acknowledgement",
                                 "severity": inc.get("severity")})
                log.warning("escalated %s: not acknowledged", inc["id"])
        except Exception as exc:  # noqa: BLE001
            log.exception("escalation loop error: %s", exc)
        await asyncio.sleep(5.0)


async def retention_loop() -> None:
    """Delete what we promised to delete, without anyone remembering to ask.

    The privacy page says seven days local and ninety days of metadata. A
    promise enforced by a button a tired person has to press is not a
    retention policy, so the same function the button calls runs on its own.
    """
    while True:
        try:
            result = await asyncio.to_thread(privacy.sweep_retention)
            if result.get("media_dirs_removed") or result.get("ledger_rows_expired"):
                log.info("retention: %s", result)
        except Exception as exc:  # noqa: BLE001
            log.exception("retention loop error: %s", exc)
        await asyncio.sleep(3600.0)


async def fail_open(incident: dict, exc: Exception) -> None:
    """Last resort: notify from the template, record why, release the queue."""
    # A fail-open row is a *replacement* for a decision that never arrived. If
    # one did arrive, writing this anyway is how a good model decision gets
    # buried under a template: an incident was once found `notified` with
    # attempts=4 and a phase-9 row on top of a phase-2 row that had succeeded.
    current = db.get_incident(incident["id"]) or {}
    if (current.get("decision") or {}).get("model") not in (None, "", "template"):
        db.set_status(incident["id"], "notified", current.get("severity"))
        db.record_failure(incident["id"], f"{type(exc).__name__}:already_decided")
        log.warning("fail-open skipped %s: a decision already exists", incident["id"])
        return
    floor, _ = effective_floor(incident)
    decision = policy.template_decision(floor, incident, PROFILE,
                                        incident_clock(incident))
    stored = db.add_decision(incident["id"], 9, floor.severity, floor.reasons, decision,
                             "template", PROMPT_VERSION,
                             [f"enrich_failed:{type(exc).__name__}"], 0)
    db.set_status(incident["id"], "notified", stored)
    db.record_failure(incident["id"], type(exc).__name__)
    if (decision.notify_now or floor.notifies) and stored == decision.severity:
        await notify.dispatch({"incident_id": incident["id"],
                               "title": decision.title, "body": decision.body,
                               "severity": decision.severity}, 9, broadcast)
    await broadcast({"type": "incident.decided", "id": incident["id"],
                     "severity": stored, "title": decision.title,
                     "degraded": True})
    log.error("fail-open notified %s from template", incident["id"])


# --------------------------------------------------------------------------
# memory digest and weekly letter (the slow loops)
# --------------------------------------------------------------------------
# Words a person would use. The model writes what it is handed, so schema
# values never reach it: `fall_confirmed` becomes "a fall", `stayed_home`
# becomes "handled without contacting you".
TYPE_NAMES = {
    "door_open": "the front door opening",
    "door_approach": "walking to the front door",
    "door_left_open": "the front door left open",
    "fall_suspected": "a possible fall",
    "fall_confirmed": "a fall",
    "fall_assisted": "a fall with someone helping",
    "stove_unattended": "something left on the hob",
    "motion_only": "movement",
    "pet_motion": "movement that turned out to be an animal",
    "person_unwell_still": "a long still period",
    "camera_offline": "a camera gap",
    "device_offline": "a device gap",
    "restlessness": "repeated pacing",
    "help_call": "a call for help",
    "unexpected_presence": "someone else in the house after dark",
    "checkin_no_response": "no answer to a check-in",
}
ROOM_NAMES = {
    "front_door": "the front door", "hallway": "the hallway",
    "kitchen": "the kitchen", "living_room": "the living room",
    "bathroom_door": "the bathroom", "prayer_room": "the prayer room",
    "bedroom_door": "the bedroom door",
}
SEVERITY_WORDS = {"info": "noticed", "watch": "noticed",
                  "urgent": "we called you", "emergency": "we called you"}


async def refresh_memory() -> dict:
    """Ask Muse Spark to fold recent structured events into household memory.

    The facts are what the evidence agent reads before deciding whether a
    finding is unusual. They are made only from rows, expire after 30 days, and
    can be deleted from the privacy page with the rest of the household data.
    """
    events = db.recent_events(120)
    items = db.list_incidents(80)
    if not events and not items:
        return {"ok": True, "facts": 0}
    summary = [{"what": TYPE_NAMES.get(str(e.get("type")), "something"),
                "where": ROOM_NAMES.get(str(e.get("zone_id")), "home"),
                "at": str(e.get("ts") or "")[11:16],
                "quiet": bool(e.get("suppressed")),
                "how_urgent": SEVERITY_WORDS.get(str(e.get("severity")), "noticed")}
               for e in events[:50]]
    if not summary:
        summary = [{"what": TYPE_NAMES.get(str(i["type"]), "something"),
                    "where": ROOM_NAMES.get(str(i.get("zone_id")), "home"),
                    "at": str(i.get("local_time") or "")[11:16],
                    "quiet": False,
                    "how_urgent": SEVERITY_WORDS.get(str(i.get("severity")), "noticed")}
                   for i in items[:40]]
    raw, usage = await asyncio.to_thread(
        meta().decide,
        {"day": summary, "known_facts": [m["fact"] for m in db.memory(HOUSEHOLD)]},
        DIGEST_PROMPT, DIGEST_SCHEMA, "low",
    )
    db.add_outbound(None, "api.meta.ai", "memory_digest", len(json.dumps(summary)),
                    usage.output_tokens, "30d", False,
                    latency_ms=usage.latency_ms, model=SPARK_MODEL)
    expires = (datetime.now(TZ) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    facts = raw.get("facts") or []
    db.clear_memory(HOUSEHOLD)
    for f in facts[:10]:
        if isinstance(f, dict) and f.get("fact"):
            db.add_memory(HOUSEHOLD, str(f["fact"])[:200],
                          float(f.get("confidence", 0.5)), "memory", expires)
    return {"ok": True, "facts": len(facts[:10])}


@app.post("/api/maintenance/digest")
async def digest():
    """Rebuild household memory now (the same job the six-hour loop runs)."""
    try:
        return await refresh_memory()
    except MetaError as exc:
        raise HTTPException(503, f"memory unavailable: {exc}") from exc


async def memory_loop() -> None:
    while True:
        try:
            result = await refresh_memory()
            if result.get("facts"):
                log.info("household memory refreshed: %d facts", result["facts"])
        except Exception as exc:  # noqa: BLE001
            log.warning("memory refresh failed: %s", exc)
        await asyncio.sleep(6 * 3600)




@app.on_event("startup")
async def startup() -> None:
    # AEGIS_WORKER=off lets `python scripts/worker.py` own the loops instead. The
    # demo runs both in one process; the split exists because restarting the API
    # must never be able to drop a pending alert.
    if os.environ.get("AEGIS_WORKER", "on").lower() != "off":
        asyncio.create_task(worker_loop())
        asyncio.create_task(escalation_loop())
        loops = "in-process"
    else:
        loops = "external (python scripts/worker.py)"
    # Retention runs in both modes. It is a storage promise, not an alert path,
    # and it is harmless for two processes to sweep the same directory.
    asyncio.create_task(retention_loop())
    # The spoken check-in deadline belongs to the API process: phase 1, the
    # notification rows and the check-in are one ordering problem, and splitting
    # them across processes would make "no answer" fight the worker loop.
    asyncio.create_task(checkin_loop())
    asyncio.create_task(memory_loop())
    _load_profile()
    # The wall's first frame should not be the request that loads YOLO and pose.
    asyncio.create_task(asyncio.to_thread(wall.warmup))
    log.info("aegis up — media=%s hmac=%s push=%s worker=%s checkin_timeout=%ss",
             MEDIA_ROOT, "on" if EDGE_TOKEN else "off (demo)",
             "on" if notify.VAPID_PUBLIC else "off", loops, int(CHECKIN_TIMEOUT_S))


web_dir = Path(os.environ.get("WEB_DIR", "web/dist"))
if web_dir.exists():
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
