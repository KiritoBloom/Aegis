"""Edge upload signatures must cover the evidence bytes, not only the incident id."""
from __future__ import annotations

import hashlib
import hmac
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "edge"))

from aegis_edge.uploader import Uploader  # noqa: E402
from app import api  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []

    def post(self, url, data=None, files=None, headers=None, **kwargs):
        self.calls.append({"url": url, "data": data, "files": files,
                           "headers": headers})
        return SimpleNamespace(status_code=200, text="ok")


def _sig(secret: str, ts: str, body: bytes) -> str:
    return hmac.new(secret.encode(), ts.encode() + body, hashlib.sha256).hexdigest()


def test_clip_signature_covers_all_three_media_files(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"; clip.write_bytes(b"video-bytes")
    still = tmp_path / "still.jpg"; still.write_bytes(b"still-bytes")
    thumb = tmp_path / "thumb.jpg"; thumb.write_bytes(b"thumb-bytes")
    client = FakeClient()
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:bad-port")
    monkeypatch.setattr("aegis_edge.uploader.httpx.Client", lambda **kwargs: client)
    uploader = Uploader("http://backend", "secret", queue_dir=str(tmp_path / "queue"))
    assert uploader.post_clip("inc_1", clip, still, thumb)
    call = client.calls[0]
    digest = hashlib.sha256(
        b"clip\x00" + b"video-bytes" + b"still\x00" + b"still-bytes"
        + b"thumb\x00" + b"thumb-bytes"
    ).hexdigest()
    assert call["headers"]["X-Edge-Content-Sha256"] == digest
    assert call["headers"]["X-Edge-Signature"] == _sig(
        "secret", call["headers"]["X-Edge-Timestamp"],
        b"inc_1." + digest.encode())
    assert set(call["files"]) == {"clip", "still", "thumb"}


def test_audio_signature_covers_the_wav(tmp_path, monkeypatch):
    wav = tmp_path / "help.wav"; wav.write_bytes(b"wav-bytes")
    client = FakeClient()
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:bad-port")
    monkeypatch.setattr("aegis_edge.uploader.httpx.Client", lambda **kwargs: client)
    uploader = Uploader("http://backend", "secret", queue_dir=str(tmp_path / "queue"))
    assert uploader.post_audio("inc_2", wav, {"audio": {}}, {})
    call = client.calls[0]
    digest = hashlib.sha256(b"audio\x00" + b"wav-bytes").hexdigest()
    assert call["headers"]["X-Edge-Content-Sha256"] == digest
    assert call["headers"]["X-Edge-Signature"] == _sig(
        "secret", call["headers"]["X-Edge-Timestamp"],
        b"inc_2." + digest.encode())


def test_backend_rejects_a_swapped_digest_or_old_id_only_signature(monkeypatch):
    monkeypatch.setattr(api, "EDGE_TOKEN", "secret")
    digest = hashlib.sha256(b"clip\x00" + b"real").hexdigest()
    ts = str(int(time.time()))
    good = _sig("secret", ts, b"inc_3." + digest.encode())
    api.verify_upload(ts, good, "inc_3", digest, digest)
    try:
        api.verify_upload(ts, good, "inc_3", "other", digest)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("a mismatched digest was accepted")
