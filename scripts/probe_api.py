"""Smoke tests for the Meta Model API. Run: python scripts/probe_api.py [name]

Never prints the key. Each probe prints HTTP status and a truncated body so the
real request/response shape is visible before any client code is written.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("META_API_BASE", "https://api.meta.ai/v1")
KEY = os.environ.get("META_API_KEY", "")


def call(method: str, path: str, payload=None, timeout=60):
    url = f"{BASE}{path}"
    data = None
    headers = {"Authorization": f"Bearer {KEY}"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def show(name: str, status: int, body: str, limit: int = 1400) -> None:
    print(f"\n=== {name} -> HTTP {status}")
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, indent=2)[:limit])
    except json.JSONDecodeError:
        print(body[:limit])


def p_models():
    show("models", *call("GET", "/models"))


def p_text():
    show(
        "responses/text",
        *call(
            "POST",
            "/responses",
            {
                "model": "muse-spark-1.3",
                "input": "Reply with exactly: ok",
                "max_output_tokens": 16,
            },
        ),
    )


def p_schema():
    schema = {
        "type": "object",
        "properties": {
            "severity": {"type": "string", "enum": ["watch", "urgent", "emergency"]},
            "title": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["severity", "title", "confidence"],
        "additionalProperties": False,
    }
    show(
        "responses/structured",
        *call(
            "POST",
            "/responses",
            {
                "model": "muse-spark-1.3",
                "input": "A person is lying on the hallway floor and has not moved for 12 seconds. Decide.",
                "max_output_tokens": 300,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "decision",
                        "schema": schema,
                        "strict": True,
                    }
                },
            },
        ),
    )


def p_vision():
    show(
        "responses/vision",
        *call(
            "POST",
            "/responses",
            {
                "model": "muse-spark-1.3",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "What is in this image? One line."},
                            {
                                "type": "input_image",
                                "image_url": "https://images.pexels.com/photos/3768131/pexels-photo-3768131.jpeg?w=640",
                            },
                        ],
                    }
                ],
                "max_output_tokens": 80,
            },
        ),
    )


def p_sam():
    show(
        "sam/image",
        *call(
            "POST",
            "/responses",
            {
                "model": "sam-3.1",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "person"},
                            {
                                "type": "input_image",
                                "image_url": "https://images.pexels.com/photos/3768131/pexels-photo-3768131.jpeg?w=640",
                            },
                        ],
                    }
                ],
            },
            timeout=120,
        ),
        limit=900,
    )


def p_asr():
    # Shape is unverified; probe the documented path with a tiny silent WAV.
    show("asr (probe only)", *call("POST", "/asr/transcribe", {}, timeout=30))


PROBES = {
    "models": p_models,
    "text": p_text,
    "schema": p_schema,
    "vision": p_vision,
    "sam": p_sam,
    "asr": p_asr,
}

if __name__ == "__main__":
    if not KEY:
        sys.exit("META_API_KEY not set in the environment")
    names = sys.argv[1:] or list(PROBES)
    for n in names:
        if n in PROBES:
            PROBES[n]()
        else:
            print(f"unknown probe: {n} (have: {', '.join(PROBES)})")
