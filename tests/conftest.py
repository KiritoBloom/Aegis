"""Keep the test process on the same dependency set as `scripts/serve.py`.

The project vendors FastAPI/Starlette in `.vendor`; the interpreter also has a
different FastAPI installed globally. Loading the global one first makes an
`app.api` import fail with a Starlette signature mismatch, which is a test
environment problem rather than an Aegis bug. Put the vendored path in front
before any test module imports the API.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / ".vendor", ROOT / "backend", ROOT / "edge"):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)
