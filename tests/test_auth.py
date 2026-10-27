"""The door to the clips.

These are security assertions, not policy ones, so they live in their own file:
a failure here means footage is readable by someone who should not see it.
Run: python tests/test_auth.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# The module reads these at import time, so set them before importing it.
os.environ["HOUSEHOLD_PIN"] = "482913"
os.environ["SESSION_SECRET"] = "test-secret-not-the-real-one"

from app import auth  # noqa: E402


def test_a_session_is_only_valid_with_the_right_signature():
    token, exp = auth.make_session()
    assert auth.session_ok(token)
    # Flip one character of the signature: an unsigned expiry is not a session.
    forged = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert not auth.session_ok(forged)


def test_an_expired_session_is_not_a_session():
    token, _ = auth.make_session(now=time.time() - auth.SESSION_S - 10)
    assert not auth.session_ok(token)


def test_a_media_link_expires():
    url = auth.media_url("inc_x", "clip.mp4", now=time.time() - auth.MEDIA_URL_S - 5)
    exp = int(url.split("exp=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    assert sig == auth.media_signature("inc_x", "clip.mp4", exp)
    assert exp < int(time.time())


def test_a_media_signature_does_not_transfer_to_another_clip():
    # Signing the path, not just the incident, is the whole point: a leaked
    # link for one alert must not unlock the rest of the household.
    exp = int(time.time()) + 60
    sig = auth.media_signature("inc_a", "clip.mp4", exp)
    assert sig != auth.media_signature("inc_b", "clip.mp4", exp)
    assert sig != auth.media_signature("inc_a", "annotated.mp4", exp)


def test_five_wrong_pins_lock_the_caller_out():
    auth._tries.clear()
    for _ in range(auth.MAX_TRIES):
        try:
            auth.check_pin("000000", "tester")
        except Exception as exc:  # HTTPException, but the type is not the point
            assert getattr(exc, "status_code", 0) == 401
    try:
        auth.check_pin("482913", "tester")
        raise AssertionError("a correct pin must not be accepted during lockout")
    except Exception as exc:
        assert getattr(exc, "status_code", 0) == 429


def test_the_right_pin_opens_a_session_and_clears_the_counter():
    auth._tries.clear()
    token, exp = auth.check_pin("482913", "tester")
    assert auth.session_ok(token) and exp > time.time()
    assert "tester" not in auth._tries


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                fails += 1
                print(f"  FAIL {name}: {exc}")
    print(f"\n{fails} failure(s)")
    sys.exit(1 if fails else 0)
