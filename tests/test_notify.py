"""A backup delivery path must be real, signed, and honest about being absent."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from app import db, notify, privacy  # noqa: E402


def test_webhook_without_configuration_is_skipped_not_failed():
    provider = notify.WebhookProvider()
    provider.urls = []
    delivery = provider.send({"incident_id": "inc_1", "title": "Test"}, 1)
    assert delivery.ok is False
    assert delivery.error == "webhook_not_configured"


def test_webhook_sends_signed_alert_text_to_primary_and_backup(monkeypatch):
    calls = []

    def fake_post(url, content, headers, timeout):
        calls.append((url, content, headers))
        if "backup" in url:
            raise OSError("backup offline")
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    monkeypatch.setattr(notify, "WEBHOOK_SECRET", "family-secret")
    provider = notify.WebhookProvider()
    provider.urls = ["https://primary.test/hook", "https://backup.test/hook"]
    delivery = provider.send({
        "incident_id": "inc_1", "title": "Margaret may have fallen",
        "body": "Please call her now.", "severity": "urgent",
    }, 2)

    assert delivery.ok is True
    assert delivery.external_id == "1 endpoint(s)"
    assert len(calls) == 2
    _url, body, headers = calls[0]
    assert b"Margaret may have fallen" in body
    assert b"clip" not in body and b"face" not in body
    assert headers["X-Aegis-Signature"].startswith("sha256=")
    assert "backup" in delivery.error


def test_dispatch_is_silent_while_monitoring_is_paused(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "notify.db"))
    db.init()
    privacy.pause("resident pressed pause", "Margaret")
    results = asyncio.run(notify.dispatch({
        "incident_id": "inc_paused", "title": "Possible fall",
        "body": "A person is on the floor.", "severity": "urgent",
    }, 1))
    assert results == [{"channel": "paused", "ok": False,
                        "status": "skipped", "error": "monitoring_paused"}]
    privacy.resume()


def test_readiness_counts_only_remote_channels(monkeypatch):
    monkeypatch.setattr(notify, "WEBHOOK_URLS", ["https://primary.test/hook"])
    monkeypatch.setattr(notify.db, "subscriptions", lambda: [])
    state = notify.readiness()
    assert state["webhook"] == {"configured": True, "endpoints": 1}
    assert state["webpush"]["devices"] == 0
    assert state["inapp"]["configured"] is True
    assert state["remote_ready"] is True

    monkeypatch.setattr(notify, "WEBHOOK_URLS", [])
    assert notify.readiness()["remote_ready"] is False


def test_sms_sends_short_text_to_every_family_number(monkeypatch):
    calls = []

    def fake_post(url, auth, data, timeout):
        calls.append((url, auth, data))
        if data["To"].endswith("9999"):
            return SimpleNamespace(status_code=500)
        return SimpleNamespace(status_code=201)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    monkeypatch.setattr(notify, "TWILIO_SID", "AC123")
    monkeypatch.setattr(notify, "TWILIO_TOKEN", "token")
    monkeypatch.setattr(notify, "TWILIO_FROM", "+15550000000")
    monkeypatch.setattr(notify, "SMS_TO", ["+15551110000", "+15552229999"])
    monkeypatch.setattr(notify, "PUBLIC_BASE_URL", "https://aegis.test")
    delivery = notify.SmsProvider().send({
        "incident_id": "inc_sms", "title": "Margaret may have fallen",
        "body": "Please call her now.", "severity": "urgent",
    }, 2)

    assert delivery.ok is True
    assert delivery.external_id == "1 number(s)"
    assert "9999" in delivery.error
    assert len(calls) == 2
    _url, auth, data = calls[0]
    assert auth == ("AC123", "token")
    assert "Margaret may have fallen" in data["Body"]
    assert "https://aegis.test/alerts/inc_sms" in data["Body"]
    assert len(data["Body"]) <= 320


def test_sms_without_configuration_is_skipped():
    delivery = notify.SmsProvider().send({"incident_id": "i", "title": "t"}, 1)
    assert delivery.ok is False
    assert delivery.error == "sms_not_configured"


def test_remote_notification_bytes_reach_the_privacy_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ledger.db"))
    db.init()

    class FakeProvider:
        name = "webhook"

        def send(self, alert, phase):
            return notify.Delivery(True, self.name, external_id="1 endpoint",
                                   bytes_sent=42)

    monkeypatch.setattr(notify, "providers", lambda: [FakeProvider()])
    asyncio.run(notify.dispatch({
        "incident_id": "inc_ledger", "title": "Possible fall",
        "body": "Please check.", "severity": "urgent",
    }, 2))
    rows = db.ledger(5)
    assert rows[0]["destination"] == "webhook"
    assert rows[0]["purpose"] == "notification_phase_2"
    assert rows[0]["bytes"] == 42
