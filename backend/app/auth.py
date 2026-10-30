"""Household PIN sessions and signed media URLs. Auth is optional on localhost and enforced when configured."""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request

PIN = os.environ.get("HOUSEHOLD_PIN", "")
SECRET = os.environ.get("SESSION_SECRET", "") or PIN or "aegis-demo-session"
COOKIE = "aegis_session"
SESSION_S = 7 * 24 * 3600
MEDIA_URL_S = 300
MAX_TRIES = 5
LOCKOUT_S = 60

_tries: dict[str, tuple[int, float]] = {}


def required() -> bool:
    return bool(PIN)


def _sign(payload: str) -> str:
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(now: float | None = None) -> tuple[str, int]:
    exp = int((now or time.time()) + SESSION_S)
    return f"{exp}.{_sign(str(exp))}", exp


def session_ok(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    exp, _, sig = token.partition(".")
    if not hmac.compare_digest(sig, _sign(exp)):
        return False
    try:
        return int(exp) > int(now or time.time())
    except ValueError:
        return False


def media_signature(incident_id: str, name: str, exp: int) -> str:
    return _sign(f"{incident_id}/{name}:{exp}")


def media_url(incident_id: str, name: str, now: float | None = None) -> str:
    exp = int((now or time.time()) + MEDIA_URL_S)
    return (f"/media/{incident_id}/{name}"
            f"?exp={exp}&sig={media_signature(incident_id, name, exp)}")


def media_ok(incident_id: str, name: str, request: Request) -> bool:
    """Cookie first, then a signed link. Both must be unforgeable."""
    if not required():
        return True
    if session_ok(request.cookies.get(COOKIE)):
        return True
    exp = request.query_params.get("exp") or ""
    sig = request.query_params.get("sig") or ""
    if not exp or not sig:
        return False
    try:
        if int(exp) <= int(time.time()):
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sig, media_signature(incident_id, name, int(exp)))


def check_pin(pin: str, who: str = "local") -> tuple[str, int]:
    """Exchange a PIN for a session token, or raise 429/401.

    The lockout is per caller so one bored teenager on the wifi cannot lock the
    family out of their own dashboard.
    """
    count, until = _tries.get(who, (0, 0.0))
    if until > time.time():
        raise HTTPException(429, f"too many attempts, try again in {int(until - time.time())}s")
    if not hmac.compare_digest(str(pin), str(PIN)):
        count += 1
        until = time.time() + LOCKOUT_S if count >= MAX_TRIES else 0.0
        _tries[who] = (0 if count >= MAX_TRIES else count, until)
        raise HTTPException(401, "wrong pin")
    _tries.pop(who, None)
    return make_session()


def client_of(request: Request) -> str:
    return request.client.host if request.client else "local"
