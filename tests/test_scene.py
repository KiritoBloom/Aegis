"""Object context must be evidence, not decoration."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "edge"))

from app import db  # noqa: E402
from app import scene as wall_scene  # noqa: E402
from aegis_edge.vision import Detection, SceneObject, support_surface  # noqa: E402


def test_support_surface_requires_the_person_to_be_on_the_object():
    person = Detection(1, 0.40, 0.40, 0.60, 0.80, 0.9)
    bed = SceneObject("bed", 0.9, 0.20, 0.30, 0.80, 0.90)
    assert support_surface(person, [bed]) is bed

    # A bed merely present in the frame is not under her.
    far_bed = SceneObject("bed", 0.9, 0.00, 0.30, 0.20, 0.90)
    assert support_surface(person, [far_bed]) is None


def test_support_surface_counts_a_lying_body_over_the_edge():
    # Feet off the end of the bed: the foot point misses, the body does not.
    person = Detection(1, 0.30, 0.55, 0.70, 0.90, 0.9)
    bed = SceneObject("bed", 0.9, 0.20, 0.40, 0.80, 0.85)
    assert support_surface(person, [bed]) is bed


def test_support_surface_ignores_non_support_objects():
    person = Detection(1, 0.40, 0.40, 0.60, 0.80, 0.9)
    chair = SceneObject("chair", 0.9, 0.30, 0.30, 0.70, 0.90)
    assert support_surface(person, [chair]) is None


def test_two_detectors_returning_one_body_are_one_person():
    a = {"box": [0.20, 0.20, 0.60, 0.90], "conf": 0.9, "posture": "lying"}
    b = {"box": [0.21, 0.21, 0.59, 0.89], "conf": 0.8, "posture": "lying"}
    assert len(wall_scene._merge_people([], [dict(a), dict(b)])) == 1
    c = {"box": [0.62, 0.20, 0.92, 0.90], "conf": 0.7, "posture": "upright"}
    assert len(wall_scene._merge_people([], [dict(a), dict(c)])) == 2


def test_recheck_uses_the_stronger_model_when_its_weights_exist(monkeypatch):
    fast, strong, pose = object(), object(), object()
    monkeypatch.setattr(wall_scene, "_models", lambda: (fast, pose))
    monkeypatch.setattr(wall_scene, "_recheck_yolo", lambda: strong)
    captured = {}

    def fake_analyze(frame, yolo, pose_model, w, h, **kwargs):
        captured.update(yolo=yolo, imgsz=kwargs.get("person_imgsz"))
        return {}

    monkeypatch.setattr(wall_scene, "_analyze", fake_analyze)
    wall_scene.analyze_frame_array(
        __import__("numpy").zeros((8, 8, 3), dtype="uint8"),
        objects=[], detect_objects=False, person_imgsz=320)

    assert captured["yolo"] is strong
    assert captured["imgsz"] == 320


def test_recheck_falls_back_to_the_fast_model_at_a_larger_input(monkeypatch):
    fast, pose = object(), object()
    monkeypatch.setattr(wall_scene, "_models", lambda: (fast, pose))
    monkeypatch.setattr(wall_scene, "_recheck_yolo", lambda: None)
    captured = {}

    def fake_analyze(frame, yolo, pose_model, w, h, **kwargs):
        captured.update(yolo=yolo, imgsz=kwargs.get("person_imgsz"))
        return {}

    monkeypatch.setattr(wall_scene, "_analyze", fake_analyze)
    wall_scene.analyze_frame_array(
        __import__("numpy").zeros((8, 8, 3), dtype="uint8"),
        objects=[], detect_objects=False, person_imgsz=320)

    assert captured["yolo"] is fast
    assert captured["imgsz"] == 448


def test_heartbeat_round_trips_the_sampled_scene(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "scene.db"))
    db.init()
    db.add_heartbeat({
        "camera_id": "cam_hallway",
        "ts_local": "2026-10-27T22:00:00+03:00",
        "fps": 15,
        "frames": 100,
        "queue_depth": 0,
        "camera_state": "online",
        "detector": "yolo",
        "objects": [{"label": "bed", "conf": 0.91, "box": [0.2, 0.3, 0.8, 0.9], "zone": "bedroom_door"}],
        "objects_at": "2026-10-27T21:59:58+03:00",
    })
    hb = db.last_heartbeat()
    assert hb["objects"][0]["label"] == "bed"
    assert hb["objects_at"] == "2026-10-27T21:59:58+03:00"


def test_first_seen_inside_a_tripwire_is_not_a_crossing():
    from aegis_edge.geometry import TrackHistory

    polygon = ((0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0))
    inside = TrackHistory()
    inside.add(0.0, 0.8, 0.5, 0.2, 0.5, 0.9)
    assert inside.entries(polygon) == 0

    crossing = TrackHistory()
    crossing.add(0.0, 0.2, 0.5, 0.2, 0.5, 0.9)
    crossing.add(1.0, 0.8, 0.5, 0.2, 0.5, 0.9)
    assert crossing.entries(polygon) == 1


def test_a_floor_fall_in_front_of_a_bed_is_not_supported():
    # The bodies overlap in perspective, but the person is below the mattress:
    # both foot and centre are outside the bed box.
    person = Detection(1, 0.30, 0.50, 0.90, 0.85, 0.9)
    bed = SceneObject("bed", 0.9, 0.20, 0.20, 0.80, 0.65)
    assert bed.overlap(person) > 0.3  # the trap the old rule fell into
    assert support_surface(person, [bed]) is None


def test_a_body_centred_on_the_bed_is_supported_even_if_feet_hang_off():
    person = Detection(1, 0.30, 0.55, 0.70, 0.90, 0.9)
    bed = SceneObject("bed", 0.9, 0.20, 0.40, 0.80, 0.85)
    assert support_surface(person, [bed]) is bed
