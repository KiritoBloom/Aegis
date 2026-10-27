"""FastAPI service: ingest, two-phase enrichment, and the family-facing API.

The alert path has two phases on purpose.

Phase 1 runs in well under a second, on the deterministic floor plus a template.
It exists so that a fall at 02:00 produces a phone buzz before SAM has even
finished segmenting the clip. Waiting for the model to be *eloquent* before
telling a daughter that her mother is on the floor is an engineering choice that
costs minutes, and minutes are the thing that matters.

Phase 2 replaces that alert in place — same notification tag, same incident id —
with the tracked clip, the evidence, and the model's words. It never re-buzzes an
acknowledged alert.

Both phases pass through the same policy floor. That is the invariant the whole
product rests on: the model may raise the alarm, never lower it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from . import clip as clip_util
from . import db, notify, policy, privacy, render
from .meta import (SAM_MODEL, SPARK_MODEL, TRANSCRIBE_MODEL, WONDER_MODEL, MetaClient,
                   MetaError, output_text, parse_sam, track_features)
from .prompt import (DECISION_SCHEMA, DIGEST_PROMPT, DIGEST_SCHEMA, LETTER_PROMPT,
                     LETTER_SCHEMA, SYSTEM_PROMPT, build_packet)

log = logging.getLogger("aegis.api")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)-12s %(message)s",
                    datefmt="%H:%M:%S")

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "data/media"))
EDGE_TOKEN = os.environ.get("EDGE_TOKEN", "")
TZ = ZoneInfo(os.environ.get("HOUSEHOLD_TZ", "Africa/Cairo"))
PROMPT_VERSION = "aegis-v2-restraint"
HOUSEHOLD = "hh_demo"

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

_subscribers: set[asyncio.Queue] = set()
_client: MetaClient | None = None
PROFILE = policy.ResidentProfile()


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
    floor = policy.evaluate(payload.model_dump(), PROFILE,
                            local_hour=incident_hour(payload.model_dump()))
    db.record_event(payload.incident_id, payload.camera_id, payload.type,
                    payload.zone_id, payload.local_time or db.now(),
                    floor.severity, suppressed=not floor.notifies,
                    reason=",".join(floor.reasons), source=payload.source)

    await broadcast({"type": "incident.created", "id": payload.incident_id,
                     "incident_type": payload.type, "floor": floor.severity})
    log.info("ingest %s %s floor=%s", payload.incident_id, payload.type, floor.severity)

    # Phase 1: notify from the floor immediately, without waiting for the model.
    asyncio.create_task(phase_one(payload.incident_id))
    return {"ok": True, "id": payload.incident_id, "floor": floor.severity}


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
# session — the household PIN
# --------------------------------------------------------------------------
class PinIn(BaseModel):
    pin: str


@app.post("/api/session")
def create_session(body: PinIn, request: Request):
    """Exchange the household PIN for a signed cookie.

    Off unless `HOUSEHOLD_PIN` is set, so the laptop demo behaves like a demo
    and a configured deployment behaves like a locked door.
    """
    if not auth.required():
        return {"ok": True, "auth": "disabled"}
    token, exp = auth.check_pin(body.pin, auth.client_of(request))
    response = JSONResponse({"ok": True, "expires_at": exp})
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_S,
                        httponly=True, samesite="lax",
                        secure=os.environ.get("COOKIE_SECURE", "") == "1")
    log.info("session opened from %s", auth.client_of(request))
    return response


@app.get("/api/session")
def session_state(request: Request):
    return {"auth_required": auth.required(),
            "signed_in": (not auth.required())
            or auth.session_ok(request.cookies.get(auth.COOKIE))}


@app.delete("/api/session")
def end_session():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE)
    return response


# --------------------------------------------------------------------------
# read API
# --------------------------------------------------------------------------
def with_media(inc: dict) -> dict:
    """Attach five-minute signed links to whatever media this incident has.

    The UI needs a URL it can put in a `<video src>`; the ledger and the alert
    page must both work from a phone that has no cookie yet, because a push
    notification opens cold. Signing per request is what makes that safe.

    The `*_path` fields are dropped: they are absolute paths on this machine,
    and the browser has no use for the layout of the laptop serving it.
    """
    for key, name in (("clip_path", "clip.mp4"), ("still_path", "still.jpg"),
                      ("thumb_path", "thumb.jpg")):
        if inc.get(key):
            inc[key.replace("_path", "_url")] = auth.media_url(inc["id"], name)
        inc.pop(key, None)
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
        },
        "zones": db.zones(HOUSEHOLD),
        "privacy": {"consent": privacy.consent_state(),
                    "pause": privacy.paused_state()},
        "timezone": str(TZ),
    }


@app.put("/api/household")
async def put_household(body: dict):
    """Update the resident profile. Validated, because a typo here changes a
    floor: `age: 8` instead of 81 would drop the high-risk reason."""
    allowed = {"display_name", "age", "cognitive_condition", "wanders",
               "uses_mobility_aid", "anticoagulant", "lives_alone"}
    changed = {}
    for k, v in (body.get("profile") or {}).items():
        if k not in allowed:
            raise HTTPException(422, f"unknown profile field {k}")
        if k == "age" and not (0 < int(v) < 130):
            raise HTTPException(422, "age out of range")
        if k in ("wanders", "uses_mobility_aid", "anticoagulant", "lives_alone"):
            v = bool(v)
        setattr(PROFILE, k, v)
        changed[k] = v
    if changed:
        db.set_household(HOUSEHOLD, {"profile": changed})
        await broadcast({"type": "household.updated", "changed": changed})
    return {"ok": True, "changed": changed}


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
        await broadcast({"type": "privacy.paused", "reason": "consent_revoked"})
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
    await broadcast({"type": "privacy.paused", **out})
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
    """Transcribe a short answer and apply the two-channel rule.

    Only ever runs after phase 1 has been sent — a check-in must never be the
    reason a fall alert is late. The transcript is untrusted input: it is
    evidence, never instruction. It can lower severity only when the perception
    layer independently agrees that she is upright and moving.
    """
    inc = db.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "unknown incident")
    if not db.notifications_for(incident_id):
        raise HTTPException(409, "no alert has been sent for this incident")

    body = await request.body()
    if not body:
        raise HTTPException(422, "expected a wav body")

    work = MEDIA_ROOT / incident_id
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "checkin.wav"
    wav.write_bytes(body)

    try:
        transcript = await asyncio.to_thread(meta().transcribe, wav)
    except MetaError as exc:
        log.error("checkin %s: transcribe failed: %s", incident_id, exc)
        raise HTTPException(503, "transcription unavailable") from exc
    finally:
        # Audio is deleted after transcription unless a family opts into
        # keeping it. Nothing here opts in.
        wav.unlink(missing_ok=True)

    cls = policy.classify_transcript(transcript)
    sam = inc.get("sam") or {}
    track = inc.get("track") or {}
    sensors = inc.get("sensors") or {}
    # Perception, not the transcript, decides whether she is on her feet. A
    # confirmed fall with no recovery is on the floor by definition — trusting a
    # `path_length` stub instead made the contradiction case resolve as
    # self-recovered, which is the single most dangerous output here.
    no_recovery = float(sensors.get("no_recovery_s") or 0)
    recovered = bool(sensors.get("recovered"))
    # `fall_confirmed` means perception saw her go down and stay down; that is
    # the definition of the event, not an inference from a field that may be
    # absent. Only an explicit `recovered` lifts it.
    on_floor = (str(track.get("posture") or "").lower() in ("lying", "fallen", "on_floor")
                or (inc.get("type") == "fall_confirmed" and not recovered)
                or (no_recovery >= 8 and not recovered))
    still_s = float(sensors.get("still_s") or no_recovery or 0)
    upright_moving = (not on_floor and not sam.get("track_unavailable") and recovered)

    verdict = policy.checkin_outcome(cls, upright_moving=upright_moving,
                                     on_floor=on_floor, still_s=still_s)

    decision = (inc.get("decision") or {})
    current = decision.get("severity") or inc.get("severity") or "urgent"
    floor = decision.get("policy_floor") or current
    delta = verdict["severity_delta"]
    if delta:
        order = list(policy.ORDER)
        idx = max(0, min(len(order) - 1, order.index(current) + delta))
        # The floor still wins. A transcript cannot take an alert below it.
        new = policy.max_severity(order[idx], floor)
    else:
        new = current

    db.add_checkin(f"chk_{incident_id[-8:]}", incident_id, transcript, verdict["resolution"],
                   TRANSCRIBE_MODEL)
    # The audio itself went to the model, and the ledger must say so. A row
    # that reports only a byte count would let "what left your home" answer
    # "some data" when the honest answer is "ten seconds of her voice".
    db.add_outbound(incident_id, "api.meta.ai", "transcribe", len(body), 0, "1h", False,
                    latency_ms=0, model=TRANSCRIBE_MODEL, contains_audio=True)
    if new != current:
        db.set_status(incident_id, "resolved" if verdict["resolution"] == "self_recovered"
                      else "acknowledged", new)

    await broadcast({"type": "incident.checkin", "id": incident_id,
                     "transcript": transcript, "class": cls,
                     "severity": new, "previous": current,
                     "contradiction": verdict["contradiction"],
                     "reason": verdict["reason"]})
    log.info("checkin %s: %s -> %s (%s)", incident_id, cls, new, verdict["reason"])
    return {"transcript": transcript, "class": cls, "severity": new,
            "previous": current, "contradiction": verdict["contradiction"],
            "resolution": verdict["resolution"], "reason": verdict["reason"],
            "model": TRANSCRIBE_MODEL}


class SimIn(BaseModel):
    scenario: str = "door"


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
    hour = now.hour
    night = hour >= 22 or hour < 6
    scenarios = {
        "door": ("door_open", "front_door", {"door_contact": "open"}),
        "fall": ("fall_confirmed", "hallway", {"no_recovery_s": 12}),
        "stove": ("stove_unattended", "kitchen", {"stove_power_w": 1240, "absent_s": 420}),
        "pet": ("pet_motion", "hallway", {"person_check": False}),
        "prayer": ("fall_suspected", "prayer_room",
                   {"posture_transitions": 8, "recurring_daily": True}),
        "caregiver": ("door_open", "front_door",
                      {"door_contact": "open", "expected_visitor": True}),
    }
    if payload.scenario not in scenarios:
        raise HTTPException(422, f"unknown scenario; expected one of {sorted(scenarios)}")
    itype, zone, sensors = scenarios[payload.scenario]
    iid = f"sim_{payload.scenario}_{int(time.time())}"
    packet = {
        "incident_id": iid, "household_id": HOUSEHOLD, "camera_id": "cam_hallway",
        "type": itype, "status": "detected", "started_at": now.isoformat(),
        "local_time": now.isoformat(), "zone_id": zone,
        "track": {"primary_track_id": 0, "track_ids": [0], "person_count": 1,
                  "path_length": 0.42, "dwell_in_door_zone_s": 6.8,
                  "crossed_door_line": itype == "door_open",
                  "absent_s": sensors.get("absent_s", 0)},
        "sensors": {**sensors, "night": night}, "privacy": {"masked": []},
        "artifacts": {}, "evidence": [f"type:{itype}", f"zone:{zone}"],
        "source": "sim",
    }
    db.upsert_incident(packet)
    floor = policy.evaluate(packet, PROFILE, local_hour=hour)
    db.record_event(iid, "cam_hallway", itype, zone, now.isoformat(), floor.severity,
                    suppressed=not floor.notifies, reason=",".join(floor.reasons),
                    source="sim")
    await broadcast({"type": "incident.created", "id": iid, "severity": floor.severity,
                     "floor": floor.severity, "sim": payload.scenario})
    asyncio.create_task(phase_one(iid))
    log.info("sim %s -> %s (floor %s)", payload.scenario, iid, floor.severity)
    return {"ok": True, "id": iid, "scenario": payload.scenario, "floor": floor.severity}


@app.get("/api/letters")
def letters():
    return {"items": db.letters()}


@app.get("/api/checkins")
def checkins():
    return {"items": db.checkins()}


@app.get("/media/{incident_id}/{name}")
def media(incident_id: str, name: str, request: Request):
    if name not in ("clip.mp4", "still.jpg", "thumb.jpg", "sam_input.mp4",
                    "annotated.mp4", "redacted.mp4"):
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
# push subscriptions
# --------------------------------------------------------------------------
class SubscriptionIn(BaseModel):
    endpoint: str
    keys: dict[str, str]
    member_id: str = "daughter"


@app.get("/api/push/public-key")
def push_public_key():
    return {"key": notify.VAPID_PUBLIC, "enabled": bool(notify.VAPID_PUBLIC)}


@app.post("/api/push/subscribe")
def push_subscribe(body: SubscriptionIn):
    import uuid
    db.add_subscription(f"sub_{uuid.uuid4().hex[:12]}", body.endpoint,
                        body.keys.get("p256dh", ""), body.keys.get("auth", ""))
    log.info("push subscription added (%d total)", len(db.subscriptions()))
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
    floor = policy.evaluate(incident, PROFILE, local_hour=local_hour)
    decision = policy.template_decision(floor, incident, PROFILE,
                                        now_local().strftime("%H:%M"))
    decision.severity = policy.max_severity(decision.severity, floor.severity)

    if not decision.notify_now and not floor.notifies:
        db.mark_phase(incident_id, 1)
        log.info("phase1 %s stayed home (floor %s)", incident_id, floor.severity)
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
async def enrich(incident: dict) -> None:
    started = time.time()
    itype = incident["type"]
    local_hour = incident_hour(incident)
    floor = policy.evaluate(incident, PROFILE, local_hour=local_hour)

    features: dict = {}
    clip = incident.get("clip_path") or ""
    if clip and Path(clip).exists():
        try:
            # Never upload the raw clip: SAM sees a short, small window cut for it.
            small = await asyncio.to_thread(
                clip_util.sam_ready, Path(clip),
                MEDIA_ROOT / incident["id"] / "sam_input.mp4",
            )
            text, sam_usage, nbytes = await asyncio.to_thread(
                meta().sam_video, small or clip, "person"
            )
            objects = parse_sam(text)
            features = track_features(objects)
            features["edge"] = incident.get("track") or {}
            # The renderer needs real masks, which are far too large for the DB;
            # it runs here and stores only its manifest.
            if objects:
                manifest = await asyncio.to_thread(
                    render.render, Path(small or clip),
                    MEDIA_ROOT / incident["id"] / "annotated.mp4", objects,
                    timestamp=datetime.fromisoformat(
                        incident.get("local_time") or datetime.now(TZ).isoformat()
                    ).strftime("%H:%M:%S"),
                )
                features["overlay"] = manifest
            db.add_outbound(incident["id"], "api.meta.ai", "sam_track",
                            nbytes, len({o.frame for o in objects}), "1h", True,
                            latency_ms=sam_usage.latency_ms, model=SAM_MODEL)
            db.attach_sam(incident["id"], features)
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
    try:
        raw, usage = await asyncio.to_thread(
            meta().decide, packet, SYSTEM_PROMPT, DECISION_SCHEMA,
            "medium" if itype.startswith("fall") else "low",
        )
        db.add_outbound(incident["id"], "api.meta.ai", "agent_decision",
                        len(json.dumps(packet)), usage.output_tokens, "90d", False,
                        latency_ms=usage.latency_ms, model=SPARK_MODEL)
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
    except (MetaError, ValueError, KeyError) as exc:
        log.warning("agent failed for %s (%s); using template", incident["id"], exc)
        decision = policy.template_decision(floor, incident, PROFILE,
                                            now_local().strftime("%H:%M"))

    # The gate is built from the packet the model actually read, so "cited a fact
    # that isn't there" and "cited a real fact in different words" are separable.
    gate = policy.EvidenceGate(packet, extra=set(incident.get("evidence") or []))
    decision, violations = policy.validate(decision, floor, gate)

    # Quiet hours hold only `watch`; the hold is stored so the UI can say
    # "held until 07:00" instead of pretending nothing happened.
    may_interrupt, why = policy.should_interrupt(decision, floor, local_hour)
    deferred_until = None
    if not may_interrupt and why == "quiet_hours":
        deferred_until = time.time() + policy.defer_until(local_hour)
        violations.append("deferred_quiet_hours")
        decision.notify_now = False

    db.add_decision(incident["id"], 2, floor.severity, floor.reasons, decision,
                    model_used, PROMPT_VERSION, violations,
                    int((time.time() - started) * 1000))
    db.set_status(incident["id"], "notified", decision.severity)

    if decision.notify_now or floor.notifies:
        alert = {"incident_id": incident["id"], "title": decision.title,
                 "body": decision.body, "severity": decision.severity}
        await notify.dispatch(alert, 2, broadcast)
        due = (time.time() + decision.follow_up_s) if decision.escalate_if_no_ack else None
        db.mark_phase(incident["id"], 2, due)
    log.info("phase2 %s -> %s (floor %s) in %dms %s%s", incident["id"],
             decision.severity, floor.severity, int((time.time() - started) * 1000),
             violations or "", " deferred" if deferred_until else "")
    await broadcast({"type": "incident.decided", "id": incident["id"],
                     "severity": decision.severity, "title": decision.title,
                     "severity_raised": decision.severity != floor.severity})


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
    floor = policy.evaluate(incident, PROFILE, local_hour=incident_hour(incident))
    decision = policy.template_decision(floor, incident, PROFILE,
                                        now_local().strftime("%H:%M"))
    db.add_decision(incident["id"], 9, floor.severity, floor.reasons, decision,
                    "template", PROMPT_VERSION, [f"enrich_failed:{type(exc).__name__}"], 0)
    db.set_status(incident["id"], "notified", decision.severity)
    db.record_failure(incident["id"], type(exc).__name__)
    if decision.notify_now or floor.notifies:
        await notify.dispatch({"incident_id": incident["id"],
                               "title": decision.title, "body": decision.body,
                               "severity": decision.severity}, 9, broadcast)
    await broadcast({"type": "incident.decided", "id": incident["id"],
                     "severity": decision.severity, "title": decision.title,
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
}
ROOM_NAMES = {
    "front_door": "the front door", "hallway": "the hallway",
    "kitchen": "the kitchen", "living_room": "the living room",
    "bathroom_door": "the bathroom", "prayer_room": "the prayer room",
    "bedroom_door": "the bedroom door",
}
SEVERITY_WORDS = {"info": "noticed", "watch": "noticed",
                  "urgent": "we called you", "emergency": "we called you"}


@app.post("/api/maintenance/digest")
async def digest():
    """Fold the last day of events into at most ten expiring facts.

    Never raw media, never transcripts: counts and times that exist in the
    database. A fact that cannot be traced to a row does not get written.
    """
    items = db.list_incidents(100)
    if not items:
        return {"ok": True, "facts": 0}
    summary = [{"what": TYPE_NAMES.get(str(i["type"]), "something"),
                "where": ROOM_NAMES.get(str(i.get("zone_id")), "home"),
                "at": str(i.get("local_time") or "")[11:16],
                "how_urgent": SEVERITY_WORDS.get(str(i.get("severity")), "noticed")}
               for i in items[:40]]
    try:
        raw, usage = await asyncio.to_thread(
            meta().decide,
            {"day": summary, "known_facts": [m["fact"] for m in db.memory(HOUSEHOLD)]},
            DIGEST_PROMPT, DIGEST_SCHEMA, "low",
        )
    except MetaError as exc:
        raise HTTPException(503, f"digest unavailable: {exc}") from exc
    db.add_outbound(None, "api.meta.ai", "memory_digest", 0, usage.output_tokens,
                    "30d", False, latency_ms=usage.latency_ms, model=SPARK_MODEL)
    expires = (datetime.now(TZ) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    facts = raw.get("facts") or []
    db.clear_memory(HOUSEHOLD)
    for f in facts[:10]:
        if isinstance(f, dict) and f.get("fact"):
            db.add_memory(HOUSEHOLD, str(f["fact"])[:200],
                          float(f.get("confidence", 0.5)), "digest", expires)
    return {"ok": True, "facts": len(facts[:10])}


@app.post("/api/maintenance/letter")
async def weekly_letter():
    """One call, two readers: the family letter and the resident's quiet report."""
    counts = db.event_counts(days=7)

    # Build the lists from `events`, not `incidents`. Events are the record of
    # what was noticed; incidents are only the subset that produced a decision.
    # Mixing the two makes the counts and the prose describe different weeks.
    happened, quiet = [], []
    for e in db.recent_events(200):
        when = str(e.get("ts") or "")[11:16]
        place = ROOM_NAMES.get(str(e.get("zone_id")), str(e.get("zone_id") or "home"))
        what = TYPE_NAMES.get(str(e.get("type")), "something")
        row = {"what": what, "where": place, "at": when}
        (quiet if e.get("suppressed") else happened).append(row)

    payload = {
        "week": {
            "moments_noticed": counts["seen_window"],
            "times_we_contacted_you": counts["alerts"],
            "moments_we_handled_without_contacting_you": counts["stayed_home"],
        },
        "rooms": {ROOM_NAMES.get(r["name"], r["name"]): r["count"]
                  for r in counts["by_zone"]},
        "kinds_of_moment": {TYPE_NAMES.get(r["name"], r["name"]): r["count"]
                            for r in counts["by_type"]},
        "times_you_were_contacted": happened[:20],
        "moments_handled_quietly": quiet[:20],
        "known_about_her": [m["fact"] for m in db.memory(HOUSEHOLD)],
    }
    try:
        raw, usage = await asyncio.to_thread(
            meta().decide, payload, LETTER_PROMPT, LETTER_SCHEMA, "low",
        )
    except MetaError as exc:
        raise HTTPException(503, f"letter unavailable: {exc}") from exc
    db.add_outbound(None, "api.meta.ai", "weekly_letter", 0, usage.output_tokens,
                    "90d", False, latency_ms=usage.latency_ms, model=WONDER_MODEL)
    week = (datetime.now(TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
    family = str(raw.get("family_letter", ""))[:2000]
    resident = str(raw.get("resident_report", ""))[:800]
    db.add_letter(f"ltr_{week}_family", HOUSEHOLD, "family", week, family, [], WONDER_MODEL)
    db.add_letter(f"ltr_{week}_resident", HOUSEHOLD, "resident", week, resident, [], WONDER_MODEL)
    return {"ok": True, "family_letter": family, "resident_report": resident}


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
    log.info("aegis up — media=%s hmac=%s push=%s worker=%s",
             MEDIA_ROOT, "on" if EDGE_TOKEN else "off (demo)",
             "on" if notify.VAPID_PUBLIC else "off", loops)


web_dir = Path(os.environ.get("WEB_DIR", "web/dist"))
if web_dir.exists():
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
