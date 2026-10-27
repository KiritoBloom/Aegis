"""Meta Model API client: Spark (judgment), SAM 3.1 (geometry), Transcribe.

Three things learned the hard way and encoded here:

1. `reasoning.effort` defaults to "high" and reasoning tokens count against
   `max_output_tokens`. A 16-token budget returns `status: incomplete` and no
   text at all. Every call therefore sets effort explicitly and budgets tokens
   for reasoning *plus* the answer.
2. SAM returns one line of text per frame, not JSON. Parsing is a regex, and a
   zero-match response is a valid result, not an error.
3. A 429 or a 5xx is not an incident. Retry twice with jitter, then fall back to
   the deterministic template.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("aegis.meta")

BASE = "https://api.meta.ai/v1"
SPARK_MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"
TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"
WONDER_MODEL = "muse-wonder-1.0"
# Back-compat aliases: the names read better in prose, the _MODEL forms cannot
# be confused with a local variable called `meta`.
SPARK = SPARK_MODEL
SAM = SAM_MODEL
TRANSCRIBE = TRANSCRIBE_MODEL
WONDER = WONDER_MODEL

# observed: ~105 reasoning tokens even for a one-word answer at effort=low
MIN_TOKENS = 900


class MetaError(RuntimeError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0

    @property
    def cost_usd(self) -> float:
        # Spark Standard: $1.25 / 1M in, $4.25 / 1M out
        return round(self.input_tokens * 1.25e-6 + self.output_tokens * 4.25e-6, 6)


def output_text(resp: dict) -> str:
    parts = []
    for item in resp.get("output") or []:
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                parts.append(c.get("text", ""))
    return "".join(parts)


class MetaClient:
    def __init__(self, api_key: str, timeout: float = 45.0) -> None:
        self.key = api_key
        self.client = httpx.Client(
            base_url=BASE, timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        self.client.close()

    # -- transport -------------------------------------------------------
    def _post(self, path: str, payload: dict, retries: int = 2) -> dict:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self.client.post(path, json=payload)
                if r.status_code == 429 or r.status_code >= 500:
                    raise MetaError(f"{r.status_code}: {r.text[:200]}")
                if r.status_code >= 400:
                    raise MetaError(f"{r.status_code}: {r.text[:400]}")
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < retries:
                    time.sleep((0.4 * 2 ** attempt) + random.random() * 0.2)
        raise MetaError(str(last))

    # -- Spark: judgment -------------------------------------------------
    def decide(self, packet: dict, system: str, schema: dict, effort: str = "low",
               max_tokens: int = 4000) -> tuple[dict, Usage]:
        started = time.time()
        budgets = [max(MIN_TOKENS, max_tokens), max(MIN_TOKENS, max_tokens) * 2]
        resp = None
        for budget in budgets:
            resp = self._post("/responses", {
                "model": SPARK,
                "instructions": system,
                "input": json.dumps(packet, separators=(",", ":")),
                "reasoning": {"effort": effort},
                "max_output_tokens": budget,
                "text": {"format": {"type": "json_schema", "name": "decision",
                                    "schema": schema, "strict": True}},
            })
            # reasoning tokens count against the budget, and their count varies per
            # call. Retrying once with double the room is cheaper than a lost alert.
            if resp.get("status") != "incomplete":
                break
            log.warning("decision incomplete at %d tokens; retrying with %d",
                        budget, budget * 2)
        assert resp is not None
        u = resp.get("usage") or {}
        usage = Usage(
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            reasoning_tokens=(u.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
            latency_ms=int((time.time() - started) * 1000),
        )
        if resp.get("status") == "incomplete":
            raise MetaError(f"incomplete response: {resp.get('incomplete_details')}")
        text = output_text(resp)
        if not text:
            raise MetaError("empty output")
        try:
            return json.loads(text), usage
        except json.JSONDecodeError:
            # a model that writes prose around JSON should not break the alert
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                raise MetaError(f"unparseable output: {text[:200]}") from None
            return json.loads(m.group(0)), usage

    def vision(self, image_bytes: bytes, prompt: str, schema: dict,
               effort: str = "low") -> tuple[dict, Usage]:
        b64 = base64.b64encode(image_bytes).decode()
        started = time.time()
        resp = self._post("/responses", {
            "model": SPARK,
            "reasoning": {"effort": effort},
            "max_output_tokens": MIN_TOKENS,
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
            ]}],
            "text": {"format": {"type": "json_schema", "name": "vision",
                                "schema": schema, "strict": True}},
        })
        u = resp.get("usage") or {}
        usage = Usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                      (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
                      int((time.time() - started) * 1000))
        text = output_text(resp)
        if not text:
            raise MetaError("empty vision output")
        return json.loads(text), usage

    # -- SAM 3.1: geometry -----------------------------------------------
    def sam_image(self, image_bytes: bytes, prompt: str = "person") -> tuple[str, Usage]:
        b64 = base64.b64encode(image_bytes).decode()
        started = time.time()
        resp = self._post("/responses", {
            "model": SAM,
            "reasoning": {"effort": "low"},
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
            ]}],
        }, retries=1)
        u = resp.get("usage") or {}
        usage = Usage(u.get("input_tokens", 0), u.get("output_tokens", 0), 0,
                      int((time.time() - started) * 1000))
        return output_text(resp), usage

    def sam_video(self, clip_path: str | Path, prompt: str = "person",
                  ) -> tuple[str, Usage, int]:
        """Upload the clip, segment it, delete it. Returns (text, usage, bytes_sent)."""
        path = Path(clip_path)
        size = path.stat().st_size
        started = time.time()
        with path.open("rb") as fh:
            up = self.client.post(
                "/files",
                files={"file": (path.name, fh, "video/mp4")},
                data={"purpose": "user_data", "expires_after": "3600"},
            )
        if up.status_code >= 400:
            raise MetaError(f"file upload failed {up.status_code}: {up.text[:200]}")
        file_id = up.json().get("id")
        if not file_id:
            raise MetaError("upload returned no file id")

        try:
            resp = self._post("/responses", {
                "model": SAM,
                "reasoning": {"effort": "low"},
                "metadata": {"mask_encoding": "one_bit", "propagation_direction": "forward"},
                "input": [{"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_video", "file_id": file_id},
                ]}],
            }, retries=1)
        finally:
            try:
                self.client.delete(f"/files/{file_id}")
            except Exception:  # noqa: BLE001
                pass

        u = resp.get("usage") or {}
        usage = Usage(u.get("input_tokens", 0), u.get("output_tokens", 0), 0,
                      int((time.time() - started) * 1000))
        return output_text(resp), usage, size

    # -- Transcribe -------------------------------------------------------
    def transcribe(self, wav_path: str | Path) -> str:
        with Path(wav_path).open("rb") as fh:
            r = self.client.post("/asr/transcribe",
                                 files={"file": (Path(wav_path).name, fh, "audio/wav")})
        if r.status_code >= 400:
            raise MetaError(f"transcribe failed {r.status_code}: {r.text[:200]}")
        body = r.json()
        return body.get("text") or body.get("transcript") or ""


# ---------------------------------------------------------------------------
# SAM wire format, via the official parser (`meta-sam-parser`).
#
#   <15f>0<|box;x1=..;y1=..;x2=..;y2=..;w=768;h=576|><|mask;x=0;y=0;data=133,72,!!!!|>
#
# The mask is `<w>,<h>,` + an encoding marker (`!` one-bit, `~` lossless) + base85
# of a bit-packed raster. Hand-rolling that decoder is how you ship an overlay
# that is subtly, silently wrong, so the official codec does the decoding and
# `_mask_raster` only re-wraps its input.
# ---------------------------------------------------------------------------
_FRAME_RE = re.compile(r"<(\d+)f>")
_REC_RE = re.compile(r"(\d+)<\|box;([^|]*)\|><\|mask;([^|]*)\|>")
_KV_RE = re.compile(r"(\w+)=([^;]+)")


@dataclass
class SamObject:
    frame: int
    obj_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    img_w: float
    img_h: float
    conf: float
    mask_data: str = ""
    mask_x: int = 0
    mask_y: int = 0

    @property
    def box_norm(self) -> tuple[float, float, float, float]:
        w = self.img_w or 1.0
        h = self.img_h or 1.0
        return (self.x1 / w, self.y1 / h, self.x2 / w, self.y2 / h)

    @property
    def foot(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.box_norm
        return ((x1 + x2) / 2, y2)

    @property
    def mask_bytes(self) -> int:
        return len(self.mask_data)

    def raster(self) -> tuple[int, int, bytes] | None:
        """Decoded `(width, height, bits)` for this mask, or None if invalid.

        `bits` is one byte per pixel, 0 or 1, row-major — cheap to threshold and
        cheap to composite, which is all the renderer needs.
        """
        return _mask_raster(self.mask_data)


def _mask_raster(data: str) -> tuple[int, int, bytes] | None:
    """`data` is `<w>,<h>,<payload>`; returns a per-pixel 0/1 raster."""
    if not data:
        return None
    parts = data.split(",", 2)
    if len(parts) != 3:
        return None
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    payload = parts[2]
    encoding = "one_bit" if payload.startswith("!") else "lossless"
    try:
        from meta_sam_parser import SegmentationMask, decode_mask_to_raster
        mask = SegmentationMask(encoding=encoding, payload=payload,
                                width=width, height=height)
        return width, height, decode_mask_to_raster(mask)
    except Exception:  # noqa: BLE001 - a bad mask must never break an alert
        return None


def parse_sam(text: str) -> list[SamObject]:
    """Parse the segmentation stream. Returns [] for a legitimate zero-match."""
    out: list[SamObject] = []
    if not text:
        return out
    # walk the text, remembering the most recent frame marker
    frame = 0
    pos = 0
    markers = [(m.start(), int(m.group(1))) for m in _FRAME_RE.finditer(text)]
    for m in _REC_RE.finditer(text):
        while pos < len(markers) and markers[pos][0] < m.start():
            frame = markers[pos][1]
            pos += 1
        box_kv = dict(_KV_RE.findall(m.group(2)))
        mask_kv = dict(_KV_RE.findall(m.group(3)))
        try:
            out.append(SamObject(
                frame=frame,
                obj_id=int(m.group(1)),
                x1=float(box_kv.get("x1", 0)), y1=float(box_kv.get("y1", 0)),
                x2=float(box_kv.get("x2", 0)), y2=float(box_kv.get("y2", 0)),
                img_w=float(box_kv.get("w", 0) or 0), img_h=float(box_kv.get("h", 0) or 0),
                conf=float(box_kv.get("c", mask_kv.get("c", 0.0)) or 0.0),
                mask_data=mask_kv.get("data", ""),
                mask_x=int(mask_kv.get("x", 0) or 0),
                mask_y=int(mask_kv.get("y", 0) or 0),
            ))
        except ValueError:
            continue
    return out


def track_features(objects: list[SamObject]) -> dict:
    """Collapse per-frame boxes into one primary track plus its trajectory."""
    if not objects:
        return {"track_unavailable": True, "frames_present": 0}

    per_id: dict[int, list[SamObject]] = {}
    for o in objects:
        per_id.setdefault(o.obj_id, []).append(o)

    primary_id = max(
        per_id,
        key=lambda k: (len(per_id[k]), sum((o.x2 - o.x1) * (o.y2 - o.y1) for o in per_id[k])),
    )
    track = sorted(per_id[primary_id], key=lambda o: o.frame)
    feet = [o.foot for o in track]
    path = sum(
        ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(feet[:-1], feet[1:])
    )
    return {
        "track_unavailable": False,
        "primary_track_id": primary_id,
        "track_ids": sorted(per_id),
        "person_count": len(per_id),
        "frames_present": len(track),
        "frames_total": len({o.frame for o in objects}),
        "first_frame": track[0].frame,
        "last_frame": track[-1].frame,
        "path_length": round(path, 4),
        "start_foot": [round(v, 4) for v in feet[0]],
        "end_foot": [round(v, 4) for v in feet[-1]],
        "median_conf": round(sorted(o.conf for o in track)[len(track) // 2], 3),
        "boxes": [[round(v, 4) for v in o.box_norm] for o in track[:60]],
    }
