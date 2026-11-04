"""When a PIN is configured, household APIs are not readable without a session."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import api, auth  # noqa: E402


def test_gate_is_open_when_no_pin_is_configured(monkeypatch):
    monkeypatch.setattr(auth, "PIN", "")
    assert api.session_gate_open("/api/incidents") is True
    assert api.session_gate_open("/media/inc/clip.mp4") is True


def test_gate_requires_a_session_for_household_apis(monkeypatch):
    monkeypatch.setattr(auth, "PIN", "482913")
    assert api.session_gate_open("/api/incidents") is False
    assert api.session_gate_open("/api/privacy") is False
    token, _exp = auth.make_session()
    assert api.session_gate_open("/api/incidents", token) is True
    # Signed edge uploads need a signing key in a locked household; media has
    # its own signed-link/session gate.
    assert api.session_gate_open("/api/ingest/incident") is False
    monkeypatch.setattr(api, "EDGE_TOKEN", "edge-secret")
    assert api.session_gate_open("/api/ingest/incident") is True
    assert api.session_gate_open("/media/inc/clip.mp4") is True
    assert api.session_gate_open("/api/session") is True
    assert api.session_gate_open("/health") is True
    assert api.session_gate_open("/_next/static/app.js") is True
    assert api.session_gate_open("/api/incidents", "forged.token") is False


def test_middleware_returns_401_and_accepts_the_session_cookie(monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(auth, "PIN", "482913")
    client = TestClient(api.app)
    assert client.get("/api/incidents").status_code == 401
    opened = client.post("/api/session", json={"pin": "482913"})
    assert opened.status_code == 200
    assert client.get("/api/incidents").status_code == 200
    assert client.get("/api/session").json()["signed_in"] is True
