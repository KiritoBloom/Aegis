"""Local frame mapping and the consent-gated Muse setup proposal."""

from __future__ import annotations

import asyncio
import json

from . import db, policy, scene, watch
from .meta import SPARK_MODEL, MetaClient
from .prompt import SETUP_PROPOSE_PROMPT

MAX_FRAME_BYTES = 5 * 1024 * 1024


async def map_frame(data: bytes) -> dict:
    """Analyze one setup frame locally and discard it."""
    if not data:
        raise ValueError("empty frame")
    if len(data) > MAX_FRAME_BYTES:
        raise ValueError("frame exceeds 5 MB")
    det = await asyncio.to_thread(scene.analyze_image_bytes, data)
    return {
        "local": True,
        "width": det["width"],
        "height": det["height"],
        "person_count": det["person_count"],
        "persons": [
            {"box": p["box"], "posture": p["posture"], "conf": p["conf"]}
            for p in (det.get("persons") or [])[:4]
        ],
        "objects": [
            {"label": o["label"], "conf": o["conf"], "box": o["box"]}
            for o in (det.get("objects") or [])[:8]
        ],
        "flags": det.get("flags") or [],
        "status": det.get("status"),
    }


def propose_plan(body: dict, client: MetaClient) -> dict:
    """Build a structured setup packet, ask Muse, and validate every mode."""
    if not body.get("consent"):
        raise ValueError("explicit consent is required to send care context to Muse")

    residents = body.get("occupants") or []
    if not isinstance(residents, list) or not residents:
        fallback = body.get("resident")
        residents = [fallback] if isinstance(fallback, dict) else []
    if not residents:
        raise ValueError("at least one occupant is required")

    occupants = []
    for person in residents[:6]:
        if not isinstance(person, dict) or not str(person.get("name") or "").strip():
            continue
        occupants.append({
            "name": str(person["name"])[:60],
            "age": int(person.get("age") or 0),
            "relationship": str(person.get("relationship") or "")[:40],
            "conditions": [
                str(condition)[:40]
                for condition in (person.get("conditions") or [])
                if str(condition).strip()
            ][:10],
        })
    resident = occupants[0] if occupants else {"name": "", "age": 0, "conditions": []}

    cameras = []
    for camera in (body.get("cameras") or [])[:6]:
        if isinstance(camera, dict):
            cameras.append({
                "name": str(camera.get("name") or "")[:60],
                "location": str(camera.get("location") or "")[:60],
                "objects": [str(item)[:32] for item in (camera.get("objects") or [])][:12],
            })

    catalog = [{
        "id": item.id,
        "label": item.label,
        "tier": item.tier,
        "locked": item.locked,
        "modes": list(item.modes if not item.locked else ("alert",)),
        "default": item.default,
    } for item in watch.CATALOG]
    packet = {
        "resident": {
            "name": resident["name"],
            "age": resident["age"],
            "conditions": resident["conditions"],
        },
        "caregiver": str(body.get("caregiver") or "")[:80],
        "other_occupants": occupants[1:],
        "cameras": cameras,
        "classes": catalog,
        "constraints": {"no_pixels": True, "no_audio": True, "self_reported_context": True},
    }
    mode_props = {
        item.id: {
            "type": "string",
            "enum": list(item.modes if not item.locked else ("alert",)),
        }
        for item in watch.CATALOG
    }
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "maxLength": 240},
            "modes": {
                "type": "object",
                "properties": mode_props,
                "required": list(mode_props),
                "additionalProperties": False,
            },
            "notes": {
                "type": "array",
                "maxItems": 3,
                "items": {"type": "string", "maxLength": 140},
            },
        },
        "required": ["summary", "modes", "notes"],
        "additionalProperties": False,
    }

    raw, usage = client.decide(packet, SETUP_PROPOSE_PROMPT, schema, "low")
    db.add_outbound(None, "api.meta.ai", "setup_proposal",
                    len(json.dumps(packet)), usage.output_tokens, "30d", False,
                    latency_ms=usage.latency_ms, model=SPARK_MODEL)

    modes = {}
    for item in watch.CATALOG:
        proposed = str((raw.get("modes") or {}).get(item.id) or "")
        allowed = list(item.modes if not item.locked else ("alert",))
        modes[item.id] = proposed if proposed in allowed else item.default

    summary = str(raw.get("summary") or "").strip()[:240]
    if not summary or any(term in summary.lower() for term in policy.FORBIDDEN_TERMS):
        summary = ("Aegis tuned the watch plan around this household. "
                   "Every choice can be changed in Settings.")
    notes = [str(note)[:140] for note in (raw.get("notes") or []) if str(note).strip()][:3]
    return {
        "summary": summary,
        "modes": modes,
        "notes": notes,
        "model": SPARK_MODEL,
        "latency_ms": usage.latency_ms,
    }
