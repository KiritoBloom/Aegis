"""Web Push subscription and delivery-test routes."""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import logging
import re
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from . import db, notify

log = logging.getLogger("aegis.push")
router = APIRouter()

# RFC 8291: an uncompressed P-256 point and a 16-byte authentication secret.
# Browsers always send both; anything else cannot be used by pywebpush.
_KEY_BYTES = {"p256dh": 65, "auth": 16}
_B64URL = re.compile(r"^[A-Za-z0-9_\-+/]+={0,2}$")


def _decoded_key(value: str) -> bytes | None:
    if not value or len(value) > 512 or not _B64URL.fullmatch(value):
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        return None


def _public_host(host: str) -> bool:
    """Reject literal loopback/private/link-local push endpoints.

    A real Web Push endpoint is a public push service. The server later POSTs
    to whatever is registered here, so accepting a literal internal address
    would turn an unauthenticated demo subscription into an SSRF probe. This
    covers the obvious forms; a hostile hostname that resolves internally is a
    resolver-level problem, not something this endpoint can settle by itself.
    """
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return False
    if lowered.isdigit():
        # Some resolvers still read this as a decimal IPv4 address.
        return False
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified)


def _valid_endpoint_value(value: str) -> str:
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("endpoint must be an HTTPS URL")
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        raise ValueError("endpoint must be an HTTPS URL") from None
    if parts.scheme.lower() != "https" or not host or not _public_host(host):
        raise ValueError("endpoint must be a public HTTPS URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("endpoint must not contain credentials")
    if parts.fragment:
        raise ValueError("endpoint must not contain a fragment")
    return value


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)
    keys: dict[str, str] = Field(max_length=8)
    member_id: str = Field(default="daughter", max_length=32, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("endpoint")
    @classmethod
    def _valid_endpoint(cls, value: str) -> str:
        return _valid_endpoint_value(value)

    @field_validator("keys")
    @classmethod
    def _valid_keys(cls, value: dict[str, str]) -> dict[str, str]:
        for name, size in _KEY_BYTES.items():
            raw = _decoded_key(value.get(name, ""))
            if raw is None:
                raise ValueError(f"{name} must be base64url")
            if len(raw) != size:
                raise ValueError(f"{name} has the wrong size")
        return value


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)

    @field_validator("endpoint")
    @classmethod
    def _valid_endpoint(cls, value: str) -> str:
        return _valid_endpoint_value(value)


def _public_error(error: str | None) -> str:
    """Provider internals stay in the log; the browser gets a reason code."""
    if not error:
        return ""
    if error == "vapid_not_configured":
        return "push is not configured on this server"
    if error == "pywebpush_missing":
        return "push support is not installed on this server"
    if error == "no_subscription":
        return "no device is registered yet"
    return "check the server log for details"


@router.get("/api/push/public-key")
def push_public_key():
    enabled = bool(notify.VAPID_PUBLIC and notify.VAPID_PRIVATE)
    return {"key": notify.VAPID_PUBLIC, "enabled": enabled}


@router.get("/api/notifications/readiness")
def notifications_readiness():
    """Remote delivery channels, so a family can see what is armed before 3 a.m."""
    return notify.readiness()


@router.post("/api/push/subscribe")
def push_subscribe(body: SubscriptionIn):
    db.add_subscription(f"sub_{uuid.uuid4().hex[:12]}", body.endpoint,
                        body.keys["p256dh"], body.keys["auth"])
    log.info("push subscription added (%d total)", len(db.subscriptions()))
    return {"ok": True}


@router.delete("/api/push/subscribe")
def push_unsubscribe(body: UnsubscribeIn):
    removed = db.drop_subscription_by_endpoint(body.endpoint)
    log.info("push subscription removed (%d row(s))", removed)
    return {"ok": True, "removed": removed}


@router.post("/api/push/test")
async def push_test():
    subs = db.subscriptions()
    if not subs:
        return {"ok": False, "sent": 0, "devices": 0,
                "error": "no device is registered yet"}
    alert = {
        "incident_id": f"test_{uuid.uuid4().hex[:12]}",
        "title": "This is what an Aegis alert looks like",
        "body": "Nothing is wrong. Tap to open the app and see a real alert from this house.",
        "severity": "watch",
    }
    delivery = await asyncio.to_thread(notify.WebPushProvider().send, alert, 1)
    db.add_outbound(None, "webpush", "test_notification", 0, 0, "immediate", False)
    public_error = "" if delivery.ok else _public_error(delivery.error)
    log.info("push test: ok=%s devices=%d reason=%s",
             delivery.ok, len(subs), public_error or "none")
    return {"ok": delivery.ok, "sent": 1 if delivery.ok else 0,
            "devices": len(subs), "error": public_error}
