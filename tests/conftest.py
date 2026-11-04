"""Import project code; dependencies come only from the active environment."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "edge"):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)


@pytest.fixture(autouse=True)
def _reset_pin_lockout_state():
    """The PIN limiter is process-wide; test attempts must not leak into peers."""
    auth = sys.modules.get("app.auth")
    if auth is not None:
        with auth._tries_lock:
            auth._tries.clear()
            auth._global_failures = 0
            auth._global_until = 0.0
    yield
    auth = sys.modules.get("app.auth")
    if auth is not None:
        with auth._tries_lock:
            auth._tries.clear()
            auth._global_failures = 0
            auth._global_until = 0.0
