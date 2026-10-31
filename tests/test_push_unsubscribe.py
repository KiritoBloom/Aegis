"""Turning alerts off must remove the device from the house, not only the browser."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from pydantic import ValidationError  # noqa: E402

from app import db, push_routes  # noqa: E402

ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-token"


def test_unsubscribe_removes_the_matching_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "push.db"))
    db.init()
    db.add_subscription("sub_1", ENDPOINT, "p" * 65, "a" * 16)
    assert len(db.subscriptions()) == 1
    out = push_routes.push_unsubscribe(push_routes.UnsubscribeIn(endpoint=ENDPOINT))
    assert out == {"ok": True, "removed": 1}
    assert db.subscriptions() == []


def test_unsubscribe_validates_the_endpoint_shape():
    for bad in ("http://fcm.googleapis.com/x", "https://localhost/x",
                "https://127.0.0.1/x", "not-a-url"):
        try:
            push_routes.UnsubscribeIn(endpoint=bad)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"accepted unsafe endpoint {bad!r}")
