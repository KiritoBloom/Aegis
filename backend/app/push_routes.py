"""Web Push subscription and delivery-test routes."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from . import db, notify

log = logging.getLogger("aegis.push")
router = APIRouter()


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: dict[str, str]
    member_id: str = "daughter"


@router.get("/api/push/public-key")
def push_public_key():
    return {"key": notify.VAPID_PUBLIC, "enabled": bool(notify.VAPID_PUBLIC)}


@router.post("/api/push/subscribe")
def push_subscribe(body: SubscriptionIn):
    db.add_subscription(f"sub_{uuid.uuid4().hex[:12]}", body.endpoint,
                        body.keys.get("p256dh", ""), body.keys.get("auth", ""))
    log.info("push subscription added (%d total)", len(db.subscriptions()))
    return {"ok": True}


@router.post("/api/push/test")
async def push_test():
    subs = db.subscriptions()
    if not subs:
        return {"ok": False, "sent": 0, "devices": 0,
                "error": "no device is registered yet"}
    alert = {
        "incident_id": f"test_{int(time.time())}",
        "title": "This is what an Aegis alert looks like",
        "body": "Nothing is wrong. Tap to open the app and see a real alert from this house.",
        "severity": "watch",
    }
    delivery = await asyncio.to_thread(notify.WebPushProvider().send, alert, 1)
    db.add_outbound(None, "webpush", "test_notification", 0, 0, "immediate", False)
    log.info("push test: ok=%s devices=%d %s", delivery.ok, len(subs), delivery.error or "")
    return {"ok": delivery.ok, "sent": 1 if delivery.ok else 0,
            "devices": len(subs), "error": delivery.error or ""}
