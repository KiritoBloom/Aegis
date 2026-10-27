"""The edge process: capture, perceive, decide, package, upload.

One loop, one thread of control, and a hard rule: the network is never on the
critical path of a decision. Every network call here is best-effort, and failure
degrades to "queued locally", never to "no alert".
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .capture import CameraSource
from .clip import RingBuffer, build, probe_ok
from .config import Config, Zone
from .geometry import TrackHistory
from .privacy import PrivacyMasker
from .state import Context, Event, Gate, RulesEngine, State, night_at
from .uploader import Uploader
from .vision import DoorRegionWatcher, MotionGate, PersonDetector, posture_for

log = logging.getLogger("aegis.edge")

EDGE_VERSION = "0.1.0"


def _primary(dets):
    """The resident is the biggest box. Two similar boxes means 'not sure who'."""
    if not dets:
        return None
    return max(dets, key=lambda d: d.area)


def _evidence_for(ev: Event) -> list[str]:
    out = []
    f = ev.features
    if ev.type.startswith("door"):
        out.append("person_track_in_door_zone")
        if f.get("dwell_in_zone_s"):
            out.append(f"dwell_{f['dwell_in_zone_s']}s")
        if f.get("night"):
            out.append("night_hours")
        if f.get("door_contact") == "open":
            out.append("door_contact_open")
        elif f.get("door_diff"):
            out.append(f"door_region_changed_{f['door_diff']}")
    if ev.type.startswith("fall"):
        out.append(f"posture_{f.get('posture', 'unknown')}")
        if f.get("lying_for_s"):
            out.append(f"lying_{f['lying_for_s']}s")
        if f.get("motion_energy") is not None:
            out.append("no_recovery")
    if ev.type == "stove_unattended":
        out.append("stove_powered")
        out.append(f"no_person_{f.get('absent_s')}s")
    return out


class EdgeRunner:
    def __init__(self, cfg: Config, secret: str, max_seconds: float | None = None) -> None:
        self.cfg = cfg
        self.max_seconds = max_seconds
        self.source = CameraSource(cfg)
        self.masker = PrivacyMasker(cfg.zones)
        self.motion = MotionGate(cfg)
        self.detector = PersonDetector(cfg)
        self.ring = RingBuffer(seconds=cfg.clip.pre_s + 3, fps=max(8, cfg.camera.fps))
        self.rules = RulesEngine(cfg)
        self.gate = Gate(cfg)
        self.uploader = Uploader(cfg.upload.backend_url, secret, cfg.upload.queue_dir,
                                 cfg.upload.timeout_s)
        self.tz = ZoneInfo(cfg.household.timezone)
        self.camera_id = "cam_hallway"

        door_zone = cfg.zone(cfg.door.zone)
        self.door_zone: Zone | None = door_zone
        self.door_watch = DoorRegionWatcher(door_zone) if door_zone else None
        self.kitchen = cfg.zone("kitchen")

        self.tracks: dict[int, TrackHistory] = {}
        self.pending: dict | None = None
        self.last_check = -1e9
        self.last_pose = -1e9
        self.last_heartbeat = -1e9
        self.kitchen_seen = -1e9
        self.posture = "unknown"
        self.posture_log: list[tuple[float, str]] = []
        self.calibrated = False
        self._last_frame = None
        self.stats = {"frames": 0, "person_frames": 0, "triggers": 0, "alerts": 0}

    # -- helpers ---------------------------------------------------------
    def _conf(self, t: float) -> float:
        local_hour = datetime.now(self.tz).hour
        return self.cfg.person.conf_night if night_at(local_hour) else self.cfg.person.conf_day

    def _posture_cycles(self, t: float, window: float = 180.0) -> int:
        recent = [p for (ts, p) in self.posture_log if t - ts <= window]
        return sum(1 for a, b in zip(recent[:-1], recent[1:]) if a != b)

    def _door_state(self) -> tuple[bool, str]:
        contact = self.source.cues.state("door_contact", "closed")
        return (str(contact) == "open"), str(contact)

    # -- incident packaging ---------------------------------------------
    def _incident(self, ev: Event, t: float, track: TrackHistory | None) -> dict:
        now = datetime.now(self.tz)
        door_open, contact = self._door_state()
        zone = self.cfg.zone(ev.zone) if ev.zone else None
        feats = dict(ev.features)
        if track is not None and zone is not None:
            feats.update(track.to_features(zone.points))
        feats.pop("last_foot", None)

        return {
            "incident_id": f"inc_{uuid.uuid4().hex[:16]}",
            "household_id": self.cfg.household.id,
            "camera_id": self.camera_id,
            "type": ev.type,
            "status": "detected",
            "started_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            "local_time": now.isoformat(),
            "zone_id": ev.zone,
            "severity_hint": ev.severity.value,
            "track": {
                "track_id": 1,
                "primary": True,
                **{k: v for k, v in feats.items() if not isinstance(v, (dict, list))},
            },
            "fall": (
                {"posture": feats.get("posture", "unknown"),
                 "lying_for_s": feats.get("lying_for_s", 0)}
                if ev.type.startswith("fall") else None
            ),
            "sensors": {
                "door_contact": contact,
                "stove_power_w": self.source.cues.state("stove_power_w", 0),
                "camera_fps": self.cfg.camera.fps,
                "edge_version": EDGE_VERSION,
                "night": night_at(now.hour),
            },
            "privacy": {
                "masks_applied": self.masker.names,
                "audio": False,
                "raw_video_left_home": False,
            },
            "artifacts": {"clip": "pending", "still": "pending", "duration_s": 0.0, "frames": 0},
            "evidence": _evidence_for(ev),
            "source": "live" if self.source.kind == "usb" else "replay",
            "edge_ts": round(t, 3),
        }

    # -- main loop -------------------------------------------------------
    def run(self) -> dict:
        if not self.source.open():
            raise SystemExit("source unavailable")
        log.info("edge up: %s (%s), detector=%s, masks=%s",
                 self.cfg.camera.source, self.source.kind, self.detector.mode, self.masker.names)

        started = time.monotonic()
        try:
            while True:
                frame, t = self.source.read()
                if frame is None:
                    log.warning("source ended or stalled")
                    break
                if self.max_seconds and (time.monotonic() - started) > self.max_seconds:
                    break

                frame = self.masker.apply(frame)
                self._last_frame = frame
                if not self.calibrated and self.door_watch is not None:
                    self.door_watch.set_reference(frame)
                    self.calibrated = True

                self.ring.add(frame, t)
                self.stats["frames"] += 1

                motion, ratio = self.motion.update(frame)
                dets = []
                if motion or self.gate.state in (State.PRESENT, State.CAPTURE):
                    interval = 1.0 / max(1.0, self.cfg.person.max_fps)
                    if t - self.last_check >= interval:
                        dets = self.detector.track(frame, self._conf(t))
                        self.last_check = t
                        primary = _primary(dets)
                        if primary is not None:
                            self.stats["person_frames"] += 1
                            if t - self.last_pose >= 0.34:
                                kp = self.detector.keypoints(frame, primary)
                                if kp:
                                    primary.keypoints = kp
                                self.posture = posture_for(primary)
                                self.posture_log.append((t, self.posture))
                                if len(self.posture_log) > 400:
                                    self.posture_log = self.posture_log[-200:]
                                self.last_pose = t
                            hist = self.tracks.setdefault(primary.track_id, TrackHistory())
                            hist.add(t, primary.foot[0], primary.foot[1],
                                     primary.x2 - primary.x1, primary.y2 - primary.y1, primary.conf)

                person = bool(dets)
                self.gate.update(t, motion, person)

                primary = _primary(dets)
                if primary is not None and self.kitchen is not None:
                    from .geometry import point_in_polygon  # noqa: PLC0415

                    if point_in_polygon(primary.foot, self.kitchen.points):
                        self.kitchen_seen = t

                world = self._world_context(t, primary)
                events = self.rules.evaluate(world)

                if events and self.gate.may_trigger(t):
                    ev = events[0]
                    log.info("EVENT %s zone=%s sev=%s", ev.type, ev.zone, ev.severity.value)
                    self.gate.triggered(t)
                    self.stats["triggers"] += 1
                    incident = self._incident(ev, t, self.tracks.get(primary.track_id) if primary else None)
                    self.uploader.post_incident(incident)
                    self.pending = {"incident": incident, "t": t, "event": ev}

                if self.pending and t >= self.pending["t"] + self.cfg.clip.post_s:
                    self._finish_pending(t)

                if t - self.last_heartbeat >= self.cfg.heartbeat_s:
                    self._heartbeat(t)
                    self.last_heartbeat = t

                if int(t) % 30 == 0 and int(t) != self.stats.get("_logged"):
                    self.stats["_logged"] = int(t)
                    log.info("t=%5.1fs frames=%d persons=%d state=%s",
                             t, self.stats["frames"], self.stats["person_frames"], self.gate.state.value)
        finally:
            self.source.release()
            self.uploader.close()
        return self.stats

    def _world_context(self, t: float, primary) -> Context:
        track = None
        zone = None
        inside_rest = False
        if primary is not None:
            track = self.tracks.get(primary.track_id)
            for z in self.cfg.public_zones():
                from .geometry import point_in_polygon  # noqa: PLC0415

                if point_in_polygon(primary.foot, z.points):
                    zone = zone or z
                    if z.rest:
                        inside_rest = True
        door_open, _ = self._door_state()
        diff = 0.0
        if self.door_watch is not None and self._last_frame is not None:
            diff = self.door_watch.diff_ratio(self._last_frame)
        local_hour = datetime.now(self.tz).hour
        return Context(
            t=t,
            track=track,
            posture=self.posture,
            person_count=1 if primary else 0,
            door_open=door_open,
            door_diff=diff,
            stove_on=bool(self.source.cues.state("stove_power_w", 0)),
            kitchen_absent_s=max(0.0, t - self.kitchen_seen) if self.kitchen_seen > -1e8 else 0.0,
            night=night_at(local_hour),
            cognitive_condition=True,  # demo care profile; the backend owns the real value
            prayer_cycles=self._posture_cycles(t),
            zone=zone,
            inside_any_rest=inside_rest,
        )

    def _finish_pending(self, t: float) -> None:
        pending = self.pending
        self.pending = None
        incident = pending["incident"]
        out_dir = Path("data/media") / incident["incident_id"]
        arts = build(self.ring, pending["t"], out_dir, incident["incident_id"],
                     pre_s=self.cfg.clip.pre_s, post_s=self.cfg.clip.post_s,
                     fps=self.cfg.clip.fps, crf=self.cfg.clip.crf)
        if arts is None:
            log.warning("no clip for %s; incident already sent", incident["incident_id"])
            self.gate.captured(t)
            return
        if not probe_ok(Path(arts.clip_path)):
            log.warning("clip failed probe; not uploading")
            self.gate.captured(t)
            return
        ok = self.uploader.post_clip(
            incident["incident_id"], Path(arts.clip_path),
            Path(arts.still_path), Path(arts.thumb_path),
        )
        self.stats["alerts"] += 1
        log.info("clip %s: %d frames, %.1f KB, uploaded=%s",
                 incident["incident_id"], arts.frames, arts.bytes / 1024, ok)
        self.gate.captured(t)

    def _heartbeat(self, t: float) -> None:
        self.uploader.heartbeat({
            "camera_id": self.camera_id,
            "ts_local": datetime.now(self.tz).isoformat(),
            "fps": self.cfg.camera.fps,
            "frames": self.stats["frames"],
            "queue_depth": len(list(Path(self.cfg.upload.queue_dir).glob("*.json"))),
            "camera_state": "online",
            "detector": self.detector.mode,
            "edge_version": EDGE_VERSION,
        })
        self.uploader.flush_queue()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
        datefmt="%H:%M:%S",
    )
