"""Household PIN sessions and signed media URLs. Auth is optional on localhost and enforced when configured."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request

log = logging.getLogger("aegis.auth")

PIN = os.environ.get("HOUSEHOLD_PIN", "")
SECRET = os.environ.get("SESSION_SECRET", "")
if not SECRET:
    if PIN:
        # A four-digit PIN is guessable offline. Never use it as the signing
        # key; a per-process secret makes stolen tokens unusable after restart.
        SECRET = secrets.token_urlsafe(32)
        log.warning("HOUSEHOLD_PIN set without SESSION_SECRET; using a volatile "
                    "signing key. Set SESSION_SECRET for sessions that survive restart.")
    else:
        SECRET = "aegis-demo-session"
COOKIE = "aegis_session"
SESSION_S = 7 * 24 * 3600
MEDIA_URL_S = 300
RESIDENT_LINK_S = 30 * 60
MAX_TRIES = 5
LOCKOUT_S = 60
# X-Forwarded-For is caller-controlled behind a loopback proxy. A per-address
# lockout alone therefore lets an attacker rotate the header and guess forever.
# A second, process-wide bucket bounds the total failure rate; a legitimate
# family loses at most a minute if an attacker is active.
MAX_GLOBAL_TRIES = 20
# The lockout table is keyed by a caller-supplied identity. Without a bound, a
# stream of unique addresses is an unbounded allocation in a long-lived process.
MAX_TRACKED_CLIENTS = 4096

_tries: dict[str, tuple[int, float]] = {}
_tries_lock = threading.Lock()
_global_failures = 0
_global_until = 0.0


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


def checkin_token(incident_id: str, now: float | None = None) -> str:
    """A short-lived link for the tablet in the resident's room."""
    exp = int((now or time.time()) + RESIDENT_LINK_S)
    return f"{exp}.{_sign(f'checkin:{incident_id}:{exp}')}"


def checkin_url(incident_id: str, now: float | None = None) -> str:
    return f"/checkin/{incident_id}?t={checkin_token(incident_id, now)}"


def checkin_ok(incident_id: str, token: str | None,
               now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    exp, _, sig = token.partition(".")
    try:
        if int(exp) <= int(now or time.time()):
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sig, _sign(f"checkin:{incident_id}:{exp}"))


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


def _remember_failure_locked(who: str, count: int, until: float, now: float) -> None:
    """Record an attempt, keeping the table bounded. Caller holds `_tries_lock`.

    Expired entries are removed first. If the table is still full of live
    lockouts, the oldest is evicted: that is a deliberate availability choice,
    because refusing to track new callers would also make the counter
    bypassable by simply changing the supplied identity.
    """
    if len(_tries) >= MAX_TRACKED_CLIENTS and who not in _tries:
        for key, (_count, expiry) in list(_tries.items()):
            if expiry <= now:
                _tries.pop(key, None)
        while len(_tries) >= MAX_TRACKED_CLIENTS:
            _tries.pop(next(iter(_tries)))
    _tries[who] = (count, until)


def check_pin(pin: str, who: str = "local") -> tuple[str, int]:
    """Exchange a PIN for a session token, or raise 429/401.

    The lockout is per caller so one bored teenager on the wifi cannot lock the
    family out of their own dashboard. The read-modify-write is locked because
    FastAPI runs these sync routes in a thread pool.
    """
    now = time.time()
    global _global_failures, _global_until
    with _tries_lock:
        if _global_until > now:
            raise HTTPException(429, "too many attempts, try again shortly")
        count, until = _tries.get(who, (0, 0.0))
        if until > now:
            raise HTTPException(429, f"too many attempts, try again in {int(until - now)}s")
        candidate = str(pin)
        # compare_digest() rejects non-ASCII str inputs; bytes work for any PIN
        # and the cap keeps a giant request body from being copied into one.
        if len(candidate) > 256 or not hmac.compare_digest(
                candidate.encode("utf-8", "surrogatepass"),
                str(PIN).encode("utf-8", "surrogatepass")):
            _global_failures += 1
            if _global_failures >= MAX_GLOBAL_TRIES:
                _global_until = now + LOCKOUT_S
                _global_failures = 0
            count += 1
            until = now + LOCKOUT_S if count >= MAX_TRIES else 0.0
            _remember_failure_locked(who, 0 if count >= MAX_TRIES else count, until, now)
            raise HTTPException(401, "wrong pin")
        _global_failures = 0
        _tries.pop(who, None)
    return make_session()


def _forwarded_ip(request: Request) -> str:
    """The first forwarded address, but only if it is actually an IP address."""
    raw = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    if not raw:
        return ""
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return ""


def client_of(request: Request) -> str:
    host = request.client.host if request.client else "local"
    # Behind the Next rewrite every request appears to come from 127.0.0.1.
    # Trust a forwarded address only from a local proxy, and only when it is a
    # real IP: an arbitrary header value must not become a lockout key or reach
    # a log line. A proxy that overwrites X-Forwarded-For is what makes this
    # meaningful; a client can still forge it, so this separates honest callers
    # rather than stopping a determined attacker.
    if host in ("127.0.0.1", "::1", "localhost"):
        forwarded = _forwarded_ip(request)
        if forwarded:
            return forwarded
    return host
