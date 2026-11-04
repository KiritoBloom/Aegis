"""Notification providers. The notification row is written before the network call."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from . import db, privacy

log = logging.getLogger("aegis.notify")

VAPID_PUBLIC = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:care@aegis.local")
# Any two HTTPS endpoints a family already trusts: a caregiver automation, an
# SMS gateway, Home Assistant, a nurse-call bridge. Nothing but alert text leaves.
WEBHOOK_URLS = [url.strip() for url in (
    os.environ.get("FAMILY_WEBHOOK_URL", ""),
    os.environ.get("FAMILY_BACKUP_WEBHOOK_URL", ""),
) if url.strip()]
WEBHOOK_SECRET = os.environ.get("FAMILY_WEBHOOK_SECRET", "")
# Optional built-in SMS via Twilio. Alert text only; the clip stays behind the
# household session. No provider SDK: one form POST is the whole contract.
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.environ.get("TWILIO_FROM", "")
SMS_TO = [n.strip() for n in os.environ.get("FAMILY_SMS_TO", "").split(",") if n.strip()]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _endpoint_label(url: str) -> str:
    """Host only. A webhook URL can carry credentials or a capability token."""
    try:
        host = urlsplit(url).hostname or "endpoint"
    except ValueError:
        host = "endpoint"
    return host[:40]


@dataclass
class Delivery:
    ok: bool
    channel: str
    external_id: str | None = None
    error: str | None = None
    bytes_sent: int = 0


class Provider(Protocol):
    name: str

    def send(self, alert: dict, phase: int) -> Delivery: ...


class WebPushProvider:
    """VAPID Web Push. Payload stays under 4 KB; the clip is behind the link."""

    name = "webpush"

    def __init__(self) -> None:
        self.enabled = bool(VAPID_PUBLIC and VAPID_PRIVATE)

    def send(self, alert: dict, phase: int) -> Delivery:
        if not self.enabled:
            return Delivery(False, self.name, error="vapid_not_configured")
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            return Delivery(False, self.name, error="pywebpush_missing")

        subs = db.subscriptions()
        if not subs:
            return Delivery(False, self.name, error="no_subscription")

        payload = json.dumps({
            "title": alert.get("title", "Aegis"),
            "body": alert.get("body", ""),
            "severity": alert.get("severity", "watch"),
            "phase": phase,
            "url": f"/alerts/{alert['incident_id']}",
            "tag": alert["incident_id"],
            "incident_id": alert["incident_id"],
            # Same tag on phase 2 so the lock screen updates instead of buzzing
            # twice for one event. Phase 3 is the silence/answer escalation:
            # that one is allowed to buzz because nobody has acknowledged it.
            "renotify": phase in (1, 3),
        })
        if len(payload.encode()) > 4096:
            payload = json.dumps({
                "title": alert.get("title", "Aegis"),
                "body": alert.get("body", "")[:120],
                "severity": alert.get("severity", "watch"),
                "phase": phase,
                "url": f"/alerts/{alert['incident_id']}",
                "tag": alert["incident_id"],
            })

        sent, errors = 0, []
        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    },
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE,
                    vapid_claims={"sub": VAPID_SUBJECT},
                )
                sent += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):
                    db.drop_subscription(sub["id"])
                    errors.append(f"gone:{sub['id'][:8]}")
                else:
                    # The exception text can contain the subscription endpoint,
                    # which is itself a capability. Record the class, not it.
                    errors.append(f"{status or 'error'}:{type(exc).__name__}")
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)

        if sent:
            return Delivery(True, self.name, external_id=f"{sent} device(s)",
                            bytes_sent=len(payload) * sent)
        return Delivery(False, self.name, error="; ".join(errors)[:300])


class WebhookProvider:
    """Signed alert text to primary and backup caregiver endpoints.

    The family's own bridge decides whether that becomes an SMS, a phone call,
    or a light on a nurse station. Aegis never sends the clip or the resident's
    face down this channel; the link stays behind the household session.
    """

    name = "webhook"

    def __init__(self) -> None:
        self.urls = list(WEBHOOK_URLS)

    def send(self, alert: dict, phase: int) -> Delivery:
        if not self.urls:
            return Delivery(False, self.name, error="webhook_not_configured")
        body = json.dumps({
            "type": "aegis.alert",
            "incident_id": alert["incident_id"],
            "title": alert.get("title", "Aegis"),
            "body": alert.get("body", ""),
            "severity": alert.get("severity", "watch"),
            "phase": phase,
            "path": f"/alerts/{alert['incident_id']}",
        }, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
        if WEBHOOK_SECRET:
            stamp = str(int(time.time()))
            headers["X-Aegis-Timestamp"] = stamp
            signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Aegis-Signature"] = f"sha256={signature}"
            # V1 keeps the documented body-only contract. V2 binds the timestamp
            # to the body, so a receiver can reject a replay outside a short
            # window; it is additive, and existing bridges keep working.
            signed = hmac.new(WEBHOOK_SECRET.encode(), f"{stamp}.".encode() + body,
                              hashlib.sha256).hexdigest()
            headers["X-Aegis-Signature-V2"] = f"sha256={signed}"
        sent, errors = 0, []
        for url in self.urls:
            label = _endpoint_label(url)
            try:
                response = httpx.post(url, content=body, headers=headers, timeout=8.0)
                if 200 <= response.status_code < 300:
                    sent += 1
                else:
                    errors.append(f"{response.status_code}@{label}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}@{label}")
        if sent:
            # A partial call tree is still a successful page, but the failed
            # backup endpoint must stay visible in the notification row.
            return Delivery(True, self.name, external_id=f"{sent} endpoint(s)",
                            error="; ".join(errors)[:300] or None,
                            bytes_sent=len(body) * sent)
        return Delivery(False, self.name, error="; ".join(errors)[:300])


class SmsProvider:
    """Twilio SMS to the family's named numbers. Short text, never media."""

    name = "sms"

    def __init__(self) -> None:
        self.enabled = bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and SMS_TO)

    def send(self, alert: dict, phase: int) -> Delivery:
        if not self.enabled:
            return Delivery(False, self.name, error="sms_not_configured")
        link = f" {PUBLIC_BASE_URL}/alerts/{alert['incident_id']}" if PUBLIC_BASE_URL else ""
        body = f"{alert.get('title', 'Aegis')}: {alert.get('body', '')}{link}"[:320]
        sent, errors = 0, []
        for number in SMS_TO:
            try:
                response = httpx.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
                    auth=(TWILIO_SID, TWILIO_TOKEN),
                    data={"To": number, "From": TWILIO_FROM, "Body": body},
                    timeout=8.0,
                )
                if 200 <= response.status_code < 300:
                    sent += 1
                else:
                    errors.append(f"{response.status_code}@{number[-4:]}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}@{number[-4:]}")
        if sent:
            return Delivery(True, self.name, external_id=f"{sent} number(s)",
                            error="; ".join(errors)[:300] or None,
                            bytes_sent=len(body) * sent)
        return Delivery(False, self.name, error="; ".join(errors)[:300])


class InAppProvider:
    """Always succeeds: the banner is the fallback when push is not available."""

    name = "inapp"

    def send(self, alert: dict, phase: int) -> Delivery:
        return Delivery(True, self.name, external_id=alert["incident_id"])


class ConsoleProvider:
    """The demo channel. Makes the alert visible in a terminal you can record."""

    name = "console"

    def send(self, alert: dict, phase: int) -> Delivery:
        log.warning("ALERT p%d [%s] %s — %s", phase, alert.get("severity"),
                    alert.get("title"), alert.get("body"))
        return Delivery(True, self.name)


_providers: list[Provider] | None = None


def providers() -> list[Provider]:
    global _providers
    if _providers is None:
        _providers = [WebPushProvider(), WebhookProvider(), SmsProvider(),
                      InAppProvider(), ConsoleProvider()]
    return _providers


def readiness() -> dict:
    """Which channels can actually reach a person away from this screen."""
    devices = len(db.subscriptions())
    return {
        "webpush": {"configured": bool(VAPID_PUBLIC and VAPID_PRIVATE),
                    "devices": devices},
        "webhook": {"configured": bool(WEBHOOK_URLS),
                    "endpoints": len(WEBHOOK_URLS)},
        "sms": {"configured": bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and SMS_TO),
                "recipients": len(SMS_TO)},
        "inapp": {"configured": True},
        "console": {"configured": True},
        "remote_ready": bool((devices and VAPID_PUBLIC and VAPID_PRIVATE) or WEBHOOK_URLS
                             or (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and SMS_TO)),
    }


async def dispatch(alert: dict, phase: int, broadcast=None) -> list[dict]:
    """Row first, then network. Never raises: a failed alert must not crash the loop."""
    if privacy.paused_state().get("paused"):
        # Pause must win over every path, including one already in flight.
        log.info("notification suppressed for %s: monitoring paused",
                 alert.get("incident_id"))
        if broadcast:
            await broadcast({"type": "incident.suppressed",
                             "id": alert.get("incident_id"),
                             "reason": "monitoring_paused"})
        return [{"channel": "paused", "ok": False, "status": "skipped",
                 "error": "monitoring_paused"}]
    results: list[dict] = []
    for provider in providers():
        if privacy.paused_state().get("paused"):
            # Pause can arrive between providers; do not let the next one send.
            results.append({"channel": "paused", "ok": False,
                            "status": "skipped", "error": "monitoring_paused"})
            break
        notif_id = f"ntf_{uuid.uuid4().hex[:12]}"
        db.add_notification(notif_id, alert["incident_id"], provider.name, phase,
                            alert.get("severity", "watch"),
                            alert.get("title", ""), alert.get("body", ""))
        try:
            delivery = await asyncio.to_thread(provider.send, alert, phase)
        except Exception as exc:  # noqa: BLE001
            # Provider exceptions can embed endpoints or credentials; persist
            # the class name, which is enough to tell a family which channel broke.
            delivery = Delivery(False, provider.name, error=type(exc).__name__)
        # A channel with no device enrolled has not failed; it has nothing to do.
        # Recording that as "failed" would put a red banner on a working system.
        unconfigured = delivery.error in ("no_subscription", "vapid_not_configured",
                                         "webhook_not_configured", "sms_not_configured")
        status = "sent" if delivery.ok else ("skipped" if unconfigured else "failed")
        db.finish_notification(notif_id, status, delivery.external_id, delivery.error)
        if delivery.bytes_sent and provider.name in ("webpush", "webhook", "sms"):
            # The push service, SMS gateway or webhook is another destination
            # outside this house. "What left your home" must include it.
            db.add_outbound(alert.get("incident_id"), provider.name,
                            f"notification_phase_{phase}", delivery.bytes_sent,
                            0, "provider", False)
        if status == "failed":
            log.error("notification failed on %s: %s", provider.name, delivery.error)
        results.append({"channel": provider.name, "ok": delivery.ok,
                        "status": status, "error": delivery.error})

    if broadcast:
        await broadcast({"type": "incident.notified", "id": alert["incident_id"],
                         "phase": phase, "severity": alert.get("severity"),
                         "channels": [r["channel"] for r in results if r["ok"]],
                          "failed": [r["channel"] for r in results if r["status"] == "failed"],
                          "skipped": [r["channel"] for r in results if r["status"] == "skipped"]})
    return results
