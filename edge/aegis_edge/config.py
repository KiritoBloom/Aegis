"""Typed access to config/edge.yaml.

Deliberately dependency-light: the edge must start on a machine that has nothing
but Python, OpenCV and ffmpeg.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Camera:
    source: str = "usb:0"
    width: int = 640
    height: int = 480
    fps: int = 15
    realtime: bool = True
    loop: bool = True


@dataclass(frozen=True)
class Household:
    id: str = "hh_demo"
    timezone: str = "Africa/Cairo"
    resident_name: str = "Margaret"


@dataclass(frozen=True)
class Motion:
    downscale: tuple[int, int] = (160, 120)
    min_changed_ratio: float = 0.008
    persistence_frames: int = 2
    cooldown_s: float = 2.0
    ignore_zones: tuple[str, ...] = ("tv", "window", "private")


@dataclass(frozen=True)
class Person:
    model: str = "yolov8n.pt"
    imgsz: int = 320
    conf_day: float = 0.45
    conf_night: float = 0.35
    min_area_ratio: float = 0.012
    max_fps: float = 8.0
    tracker: str = "bytetrack.yaml"


@dataclass(frozen=True)
class Fall:
    enabled: bool = True
    suspect_score: float = 0.60
    confirm_score: float = 0.80
    no_recovery_s: float = 8.0


@dataclass(frozen=True)
class Clip:
    pre_s: float = 6.0
    post_s: float = 4.0
    fps: int = 10
    crf: int = 26
    max_mb: float = 8.0


@dataclass(frozen=True)
class Door:
    zone: str = "front_door"
    dwell_s: float = 3.0
    region_diff_ratio: float = 0.12
    left_open_s: float = 120.0


@dataclass(frozen=True)
class Upload:
    backend_url: str = "http://127.0.0.1:8000"
    queue_dir: str = "data/queue"
    retry_max_s: float = 60.0
    timeout_s: float = 20.0


@dataclass(frozen=True)
class Zone:
    name: str
    type: str
    points: tuple[tuple[float, float], ...]
    risk: str = "medium"
    schedule: str | None = None
    rest: bool = False
    private: bool = False

    @property
    def is_exclusion(self) -> bool:
        return self.type == "exclusion" or self.private


@dataclass(frozen=True)
class Config:
    camera: Camera
    household: Household
    motion: Motion
    person: Person
    fall: Fall
    clip: Clip
    door: Door
    upload: Upload
    zones: tuple[Zone, ...] = field(default_factory=tuple)
    heartbeat_s: float = 10.0
    log_level: str = "INFO"
    path: str = ""

    def zone(self, name: str) -> Zone | None:
        for z in self.zones:
            if z.name == name:
                return z
        return None

    def public_zones(self) -> tuple[Zone, ...]:
        """Zones a person may legitimately be in. Exclusions are never tracked."""
        return tuple(z for z in self.zones if not z.is_exclusion)


def _section(cls, data: dict[str, Any], key: str):
    raw = data.get(key) or {}
    allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in raw.items() if k in allowed})


def load(path: str | Path = "config/edge.yaml") -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    zones = []
    for z in raw.get("zones") or []:
        zones.append(
            Zone(
                name=z["name"],
                type=z.get("type", "dwell"),
                points=tuple((float(x), float(y)) for x, y in z["points"]),
                risk=z.get("risk", "medium"),
                schedule=z.get("schedule"),
                rest=bool(z.get("rest", False)),
                private=bool(z.get("private", False)),
            )
        )

    upload = _section(Upload, raw, "upload")
    env_url = os.environ.get("BACKEND_URL")
    if env_url:
        upload = Upload(**{**upload.__dict__, "backend_url": env_url})

    return Config(
        camera=_section(Camera, raw, "camera"),
        household=_section(Household, raw, "household"),
        motion=_section(Motion, raw, "motion"),
        person=_section(Person, raw, "person"),
        fall=_section(Fall, raw, "fall"),
        clip=_section(Clip, raw, "clip"),
        door=_section(Door, raw, "door"),
        upload=upload,
        zones=tuple(zones),
        heartbeat_s=float(raw.get("heartbeat_s", 10)),
        log_level=str(raw.get("log_level", "INFO")),
        path=str(path),
    )
