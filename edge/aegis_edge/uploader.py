"""HMAC-signed uploads with an offline queue.

The edge never talks to the notification provider or the model API. It talks to
one backend, and if that backend is down it writes to a queue and keeps watching.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from pathlib import Path

import httpx

log = logging.getLogger("aegis.upload")


class Uploader:
    def __init__(self, backend_url: str, secret: str, queue_dir: str = "data/queue",
                 timeout_s: float = 20.0) -> None:
        self.base = backend_url.rstrip("/")
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.queue = Path(queue_dir)
        self.queue.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(timeout=timeout_s)

    # -- signing ---------------------------------------------------------
    def _sign(self, ts: str, body: bytes) -> str:
        return hmac.new(self.secret, ts.encode() + body, hashlib.sha256).hexdigest()

    def _headers(self, body: bytes) -> dict[str, str]:
        ts = str(int(time.time()))
        return {
            "X-Edge-Timestamp": ts,
            "X-Edge-Signature": self._sign(ts, body),
        }

    # -- calls -----------------------------------------------------------
    def post_incident(self, payload: dict) -> bool:
        body = json.dumps(payload, separators=(",", ":")).encode()
        try:
            r = self.client.post(
                f"{self.base}/api/ingest/incident", content=body,
                headers={**self._headers(body), "Content-Type": "application/json"},
            )
            if r.status_code in (200, 201, 202):
                return True
            log.warning("incident rejected: %s %s", r.status_code, r.text[:200])
            if r.status_code == 422:
                return False  # a malformed incident will never succeed; do not queue it
        except Exception as exc:  # noqa: BLE001
            log.warning("incident upload failed (%s); queueing", exc)
        self._queue_incident(payload)
        return False

    def post_clip(self, incident_id: str, clip: Path, still: Path | None,
                  thumb: Path | None) -> bool:
        files = {"clip": (Path(clip).name, Path(clip).read_bytes(), "video/mp4")}
        if still and Path(still).exists():
            files["still"] = (Path(still).name, Path(still).read_bytes(), "image/jpeg")
        if thumb and Path(thumb).exists():
            files["thumb"] = (Path(thumb).name, Path(thumb).read_bytes(), "image/jpeg")
        ts = str(int(time.time()))
        sig = self._sign(ts, incident_id.encode())
        try:
            r = self.client.post(
                f"{self.base}/api/ingest/clip",
                data={"incident_id": incident_id},
                files=files,
                headers={"X-Edge-Timestamp": ts, "X-Edge-Signature": sig},
            )
            if r.status_code in (200, 201, 202):
                return True
            log.warning("clip rejected: %s %s", r.status_code, r.text[:200])
        except Exception as exc:  # noqa: BLE001
            log.warning("clip upload failed (%s); keeping local copy", exc)
        return False

    def heartbeat(self, status: dict) -> bool:
        body = json.dumps(status, separators=(",", ":")).encode()
        try:
            r = self.client.post(f"{self.base}/api/ingest/heartbeat", content=body,
                                 headers=self._headers(body))
            return r.status_code < 400
        except Exception:  # noqa: BLE001
            return False

    # -- offline queue ---------------------------------------------------
    def _queue_incident(self, payload: dict) -> None:
        name = f"{payload.get('incident_id', uuid.uuid4().hex)}.json"
        try:
            (self.queue / name).write_text(json.dumps(payload), encoding="utf-8")
            log.info("queued incident %s", name)
        except Exception as exc:  # noqa: BLE001
            log.error("could not queue incident: %s", exc)

    def flush_queue(self, limit: int = 20) -> int:
        """Replay queued incidents, oldest first, once the backend is back."""
        sent = 0
        for path in sorted(self.queue.glob("*.json"))[:limit]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                path.unlink(missing_ok=True)
                continue
            body = json.dumps(payload, separators=(",", ":")).encode()
            try:
                r = self.client.post(f"{self.base}/api/ingest/incident", content=body,
                                     headers={**self._headers(body), "Content-Type": "application/json"})
                if r.status_code < 400:
                    path.unlink(missing_ok=True)
                    sent += 1
                else:
                    break
            except Exception:  # noqa: BLE001
                break
        if sent:
            log.info("flushed %d queued incidents", sent)
        return sent

    def close(self) -> None:
        self.client.close()
