"""The browser wall must have exactly one POST /api/wall/frame handler."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from starlette.testclient import TestClient  # noqa: E402

from app import api, auth  # noqa: E402


def test_wall_frame_route_accepts_a_multipart_frame(monkeypatch):
    monkeypatch.setattr(auth, "PIN", "")
    monkeypatch.setattr(api.privacy, "paused_state", lambda: {"paused": False})

    quiet = {
        "feed_id": "f1", "feed_name": "Quiet", "at": "2026-10-31T20:00:00+00:00",
        "width": 640, "height": 480, "person_count": 0, "persons": [],
        "objects": [], "flags": [], "status": "empty",
        "tracking": {"samples": 1, "person_samples": 0, "fps": 1.0,
                     "lying_for_s": 0, "alert_active": False, "recovered": False},
        "alert": None, "last_alert": None, "resolved": None,
    }
    monkeypatch.setattr(api.wall, "process", lambda *args: dict(quiet))

    client = TestClient(api.app)
    response = client.post(
        "/api/wall/frame",
        data={"session": "s1", "feed": json.dumps({"id": "f1", "name": "Quiet"})},
        files={"frame": ("f.jpg", b"not-a-jpeg", "image/jpeg")},
    )
    assert response.status_code == 200, response.text[:300]
    assert response.json()["feed_id"] == "f1"


def test_only_one_route_handles_wall_frames():
    paths = []
    for route in api.app.routes:
        if getattr(route, "path", "") == "/api/wall/frame":
            paths.append(route)
    handlers = {getattr(route, "endpoint", None).__name__ for route in paths}
    assert handlers == {"wall_frame"}, handlers
