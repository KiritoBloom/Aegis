"""Notification providers. The notification row is written before the network call."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Protocol

from . import db

log = logging.getLogger("aegis.notify")

VAPID_PUBLIC = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:care@aegis.local")


@dataclass
class Delivery:
    ok: bool
    channel: str
    external_id: str | None = None
    error: str | None = None


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
                    errors.append(f"{status}:{str(exc)[:80]}")
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc)[:80])

        if sent:
            return Delivery(True, self.name, external_id=f"{sent} device(s)")
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
        _providers = [WebPushProvider(), InAppProvider(), ConsoleProvider()]
    return _providers


async def dispatch(alert: dict, phase: int, broadcast=None) -> list[dict]:
    """Row first, then network. Never raises: a failed alert must not crash the loop."""
    results: list[dict] = []
    for provider in providers():
        notif_id = f"ntf_{uuid.uuid4().hex[:12]}"
        db.add_notification(notif_id, alert["incident_id"], provider.name, phase,
                            alert.get("severity", "watch"),
                            alert.get("title", ""), alert.get("body", ""))
        try:
            delivery = await asyncio.to_thread(provider.send, alert, phase)
        except Exception as exc:  # noqa: BLE001
            delivery = Delivery(False, provider.name, error=f"{type(exc).__name__}: {exc}")
        # A channel with no device enrolled has not failed; it has nothing to do.
        # Recording that as "failed" would put a red banner on a working system.
        unconfigured = delivery.error in ("no_subscription", "vapid_not_configured")
        status = "sent" if delivery.ok else ("skipped" if unconfigured else "failed")
        db.finish_notification(notif_id, status, delivery.external_id, delivery.error)
        if status == "failed":
            log.error("notification failed on %s: %s", provider.name, delivery.error)
        results.append({"channel": provider.name, "ok": delivery.ok,
                        "status": status, "error": delivery.error})

    if broadcast:
        await broadcast({"type": "incident.notified", "id": alert["incident_id"],
                         "phase": phase, "severity": alert.get("severity"),
                         "channels": [r["channel"] for r in results if r["ok"]],
                         "failed": [r["channel"] for r in results if not r["ok"]]})
    return results
