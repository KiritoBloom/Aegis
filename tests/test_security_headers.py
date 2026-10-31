"""Security assertions for the browser edge, auth identity, and outbound channels.

Run: python -m pytest -q tests/test_security_headers.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app import db, notify  # noqa: E402

P256DH = base64.urlsafe_b64encode(b"\x04" + b"\x11" * 64).rstrip(b"=").decode("ascii")
AUTH_SECRET = base64.urlsafe_b64encode(b"\x22" * 16).rstrip(b"=").decode("ascii")
SUBSCRIPTION = {
    "endpoint": "https://push.example.test/send/capability-token",
    "keys": {"p256dh": P256DH, "auth": AUTH_SECRET},
    "member_id": "daughter",
}


# ---------------------------------------------------------------------------
# Next.js headers
# ---------------------------------------------------------------------------
def _next_headers() -> tuple[str, dict[str, str]]:
    source = (ROOT / "web" / "next.config.ts").read_text(encoding="utf-8")
    pairs = dict(re.findall(r'key:\s*"([^"]+)"\s*,\s*value:\s*"([^"]+)"', source))
    return source, pairs


def test_next_config_emits_conservative_security_headers():
    source, headers = _next_headers()
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    # A CSP that names script-src/default-src without auditing Next's inline
    # bootstrap would break the app. This test pins the deliberately minimal one.
    csp = headers["Content-Security-Policy"]
    assert "default-src" not in csp and "script-src" not in csp and "style-src" not in csp
    policy = headers["Permissions-Policy"]
    assert "camera=(self)" in policy and "microphone=(self)" in policy
    assert "geolocation=()" in policy
    assert "async headers()" in source
    assert 'source: "/:path*"' in source
    assert "poweredByHeader: false" in source


# ---------------------------------------------------------------------------
# Caller identity and lockout
# ---------------------------------------------------------------------------
def _request(host: str = "127.0.0.1", forwarded: str | None = None):
    headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)


def test_forwarded_identity_must_be_a_real_ip_address():
    from app import auth

    assert auth.client_of(_request(forwarded="203.0.113.9, 10.0.0.1")) == "203.0.113.9"
    assert auth.client_of(_request(forwarded="2001:0db8::1")) == "2001:db8::1"
    # Garbage must not become a lockout key (or a log value).
    assert auth.client_of(_request(forwarded="not-an-ip")) == "127.0.0.1"
    # A non-local peer's own address wins; its forwarded header is not trusted.
    assert auth.client_of(_request(host="198.51.100.4",
                                   forwarded="203.0.113.9")) == "198.51.100.4"
    assert auth.client_of(SimpleNamespace(client=None, headers={})) == "local"


def test_lockout_table_stays_bounded(monkeypatch):
    from app import auth

    auth._tries.clear()
    monkeypatch.setattr(auth, "MAX_TRACKED_CLIENTS", 4)
    try:
        for index in range(25):
            with pytest.raises(HTTPException) as caught:
                auth.check_pin("wrong", f"caller-{index}")
            assert caught.value.status_code == 401
        assert len(auth._tries) <= 4
    finally:
        auth._tries.clear()


@pytest.mark.parametrize("pin", ["4829\u00e93", "\ud800"])
def test_malformed_pin_is_a_normal_rejection(monkeypatch, pin):
    from app import auth

    auth._tries.clear()
    monkeypatch.setattr(auth, "PIN", "482913")
    try:
        with pytest.raises(HTTPException) as caught:
            auth.check_pin(pin, "unicode-caller")
        assert caught.value.status_code == 401
    finally:
        auth._tries.clear()


# ---------------------------------------------------------------------------
# Signed webhook
# ---------------------------------------------------------------------------
def test_webhook_timestamp_is_bound_to_the_signature(monkeypatch):
    calls = []

    def fake_post(url, content, headers, timeout):
        calls.append((url, content, headers))
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    monkeypatch.setattr(notify, "WEBHOOK_SECRET", "family-secret")
    provider = notify.WebhookProvider()
    provider.urls = ["https://hooks.example.test/aegis"]
    delivery = provider.send({
        "incident_id": "inc_1", "title": "Margaret may have fallen",
        "body": "Please call her now.", "severity": "urgent",
    }, 2)

    assert delivery.ok is True
    _url, body, headers = calls[0]
    stamp = headers["X-Aegis-Timestamp"]
    assert stamp.isdigit() and abs(int(stamp) - time.time()) < 5

    # The documented V1 contract is unchanged: HMAC over the raw body.
    legacy = hmac.new(b"family-secret", body, hashlib.sha256).hexdigest()
    assert headers["X-Aegis-Signature"] == f"sha256={legacy}"

    # V2 binds that same timestamp to the body so a receiver can reject replays.
    bound = hmac.new(b"family-secret", stamp.encode() + b"." + body,
                     hashlib.sha256).hexdigest()
    assert headers["X-Aegis-Signature-V2"] == f"sha256={bound}"
    payload = json.loads(body)
    assert payload["incident_id"] == "inc_1" and payload["phase"] == 2


def test_webhook_without_a_secret_sends_no_pseudo_signature_headers(monkeypatch):
    calls = []

    def fake_post(url, content, headers, timeout):
        calls.append(headers)
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    monkeypatch.setattr(notify, "WEBHOOK_SECRET", "")
    provider = notify.WebhookProvider()
    provider.urls = ["https://hooks.example.test/aegis"]
    assert provider.send({"incident_id": "inc_0", "title": "Test"}, 1).ok is True

    headers = calls[0]
    assert "X-Aegis-Timestamp" not in headers
    assert "X-Aegis-Signature" not in headers
    assert "X-Aegis-Signature-V2" not in headers


def test_webhook_error_does_not_leak_url_credentials(monkeypatch):
    def fail(url, content, headers, timeout):
        raise OSError("unreachable")

    monkeypatch.setattr(notify.httpx, "post", fail)
    monkeypatch.setattr(notify, "WEBHOOK_SECRET", "")
    provider = notify.WebhookProvider()
    provider.urls = ["https://bridge:super-secret@hooks.example.test/hook"]
    delivery = provider.send({"incident_id": "inc_2", "title": "Test"}, 1)

    assert delivery.ok is False
    assert "super-secret" not in delivery.error
    assert "hooks.example.test" in delivery.error


def test_webpush_error_does_not_echo_the_subscription_endpoint(monkeypatch):
    class FakeWebPushException(Exception):
        response = None

    def fail(**kwargs):
        raise FakeWebPushException(f"POST {kwargs['subscription_info']['endpoint']} failed")

    fake_module = types.ModuleType("pywebpush")
    fake_module.WebPushException = FakeWebPushException
    fake_module.webpush = fail
    monkeypatch.setitem(sys.modules, "pywebpush", fake_module)
    monkeypatch.setattr(notify, "VAPID_PUBLIC", "public")
    monkeypatch.setattr(notify, "VAPID_PRIVATE", "private")
    monkeypatch.setattr(notify.db, "subscriptions", lambda: [{
        "id": "sub_abcdef123456", "endpoint": SUBSCRIPTION["endpoint"],
        "p256dh": P256DH, "auth": AUTH_SECRET,
    }])

    delivery = notify.WebPushProvider().send({"incident_id": "inc_3", "title": "Test"}, 1)

    assert delivery.ok is False
    assert "capability-token" not in (delivery.error or "")
    assert "FakeWebPushException" in (delivery.error or "")


def test_dispatch_does_not_persist_provider_exception_text(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "dispatch.db"))
    db.init()

    class Boom:
        name = "webhook"

        def send(self, alert, phase):
            raise RuntimeError("https://bridge:capability-token@example.test")

    monkeypatch.setattr(notify, "providers", lambda: [Boom()])
    results = asyncio.run(notify.dispatch({
        "incident_id": "inc_4", "title": "Test", "body": "Check.",
    }, 1))

    assert results[0]["status"] == "failed"
    assert results[0]["error"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Push subscription route
# ---------------------------------------------------------------------------
def _push_client(monkeypatch, tmp_path) -> TestClient:
    from app import api, auth

    monkeypatch.setattr(auth, "PIN", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "push.db"))
    db.init()
    return TestClient(api.app)


@pytest.mark.parametrize("endpoint", [
    "http://push.example.test/send/abc",
    "https://user:pass@push.example.test/send/abc",
    "https://127.0.0.1/send/abc",
    "https://127.0.0.1./send/abc",
    "https://2130706433/send/abc",
    "https://[::1]/send/abc",
    "not a url",
])
def test_push_subscribe_rejects_unsafe_endpoints(monkeypatch, tmp_path, endpoint):
    client = _push_client(monkeypatch, tmp_path)
    response = client.post("/api/push/subscribe",
                           json={**SUBSCRIPTION, "endpoint": endpoint})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("keys", [
    {"p256dh": "not-base64!", "auth": AUTH_SECRET},
    {"p256dh": P256DH, "auth": "short"},
    {"auth": AUTH_SECRET},
])
def test_push_subscribe_requires_valid_webpush_keys(monkeypatch, tmp_path, keys):
    client = _push_client(monkeypatch, tmp_path)
    response = client.post("/api/push/subscribe",
                           json={**SUBSCRIPTION, "keys": keys})
    assert response.status_code == 422, response.text


def test_push_subscribe_accepts_a_well_formed_subscription(monkeypatch, tmp_path):
    client = _push_client(monkeypatch, tmp_path)
    first = client.post("/api/push/subscribe", json=SUBSCRIPTION)
    second = client.post("/api/push/subscribe", json=SUBSCRIPTION)

    assert first.status_code == 200 and second.status_code == 200
    rows = db.subscriptions()
    assert len(rows) == 1
    assert rows[0]["endpoint"] == SUBSCRIPTION["endpoint"]
    assert rows[0]["p256dh"] == P256DH


def test_push_test_hides_provider_error_details(monkeypatch, tmp_path, caplog):
    client = _push_client(monkeypatch, tmp_path)
    db.add_subscription("sub_1", SUBSCRIPTION["endpoint"], P256DH, AUTH_SECRET)

    class FakeProvider:
        def send(self, alert, phase):
            return notify.Delivery(
                False, "webpush",
                error="https://push.example.test/send/capability-token failed")

    monkeypatch.setattr(notify, "WebPushProvider", FakeProvider)
    with caplog.at_level(logging.INFO, logger="aegis.push"):
        response = client.post("/api/push/test")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False and body["devices"] == 1
    assert "capability-token" not in response.text
    assert "capability-token" not in caplog.text
    assert "server log" in body["error"]
