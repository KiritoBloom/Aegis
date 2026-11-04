"""Joints must describe the target body in the original input coordinate system."""
from types import SimpleNamespace

import numpy as np
import pytest

from app import scene


def test_seated_upright_torso_is_not_reported_as_standing():
    joints = [[0, 0, 0] for _ in range(17)]
    for i, point in {5: [.5, .27, .99], 6: [.4, .265, .99], 11: [.48, .525, .99],
                     12: [.41, .515, .99], 13: [.46, .589, .99], 14: [.36, .573, .99],
                     15: [.447, .881, .99], 16: [.357, .87, .99]}.items():
        joints[i] = point
    assert scene._posture(joints, 768, 432) == "sitting"
    # Straighten the thighs while retaining confident hips and ankles.
    joints[13], joints[14] = [.48, .7, .99], [.41, .7, .99]
    assert scene._posture(joints, 768, 432) == "upright"


def test_seated_classification_needs_both_observed_legs():
    joints = [[0, 0, 0] for _ in range(17)]
    for i, point in {5: [.4, .2, .9], 6: [.6, .2, .9], 11: [.4, .5, .9],
                     12: [.6, .5, .9], 13: [.4, .54, .9], 15: [.4, .85, .9]}.items():
        joints[i] = point
    assert scene._posture(joints, 768, 432) == "upright"


def test_invalid_and_out_of_target_joints_are_not_drawn():
    points = [[40, 30, .9], [0, 0, .9], [99, 90, .99], [float("nan"), 20, .9]]
    result = scene._skeleton(points, (20, 20, 80, 80), 100, 100)
    assert result[0] == [.4, .3, .9]
    assert [p[2] for p in result[1:]] == [0, 0, 0]


def test_crop_refinement_translates_joints_back_to_the_target_frame():
    class Pose:
        def predict(self, frame, **kwargs):
            # Crop starts at (190,90), so the shoulder must be (230,130).
            kp = np.array([[[40, 40, .95] for _ in range(17)]], dtype=float)
            kp[0, 11:13, 1] = 100
            return [SimpleNamespace(keypoints=SimpleNamespace(data=kp),
                boxes=SimpleNamespace(xyxy=np.array([[10, 10, 210, 210]])))]
    result = scene._pose_for_crop(Pose(), np.zeros((500, 1000, 3), dtype=np.uint8), (.2, .2, .4, .6))
    assert result["skeleton"][5] == [.23, .26, .95]
    assert result["posture"] == "upright"


def test_overlapping_people_keep_their_own_skeletons():
    def person(box, x):
        return {"box": box, "conf": .9, "posture": "upright", "keypoints": 17,
                "skeleton": [[x, .4, .9] for _ in range(17)]}
    a = person([.1, .1, .7, .9], .25)
    b = person([.15, .15, .65, .85], .55)
    result = scene._merge_people([], [a, b])
    assert len(result) == 2
    assert {p["skeleton"][5][0] for p in result} == {.25, .55}


def pose_result(box, points, confidence=.9):
    return SimpleNamespace(names={0: "person"},
        boxes=SimpleNamespace(xyxy=np.array([box]), cls=np.array([0]), conf=np.array([confidence])),
        keypoints=SimpleNamespace(data=np.array([points])))


@pytest.mark.parametrize("rotation,box,point,shape", [
    (90, [10, 20, 50, 80], [29, 40, .95], (100, 60, 3)),
    (180, [20, 10, 80, 50], [59, 29, .95], (60, 100, 3)),
    (270, [10, 20, 50, 80], [30, 59, .95], (100, 60, 3)),
])
def test_rotated_pose_maps_boxes_and_joints_back_to_a_non_square_frame(rotation, box, point, shape):
    class Pose:
        def predict(self, frame, **kwargs):
            assert frame.shape == shape and frame.flags.c_contiguous
            return [pose_result(box, [point, [0, 0, .99]])]
    person = scene._pose_people(Pose(), np.zeros((60, 100, 3), dtype=np.uint8), rotation)[0]
    assert person["box"] == [.2, round(10 / 60, 4), .8, round(50 / 60, 4)]
    assert person["skeleton"][0] == [.4, .5, .95]
    assert person["skeleton"][1] == [0, 0, 0]


def test_prone_rotation_follows_the_observed_torso_in_pixel_space():
    person = {"posture": "lying", "box": [.1, .6, .9, .9],
              "skeleton": [[0, 0, 0] for _ in range(17)]}
    for i in (5, 6): person["skeleton"][i] = [.7, .7, .9]
    for i in (11, 12): person["skeleton"][i] = [.4, .75, .9]
    assert scene._prone_rotation(person, 768, 432) == 270
    for i in (11, 12): person["skeleton"][i] = [.9, .75, .9]
    assert scene._prone_rotation(person, 768, 432) == 90
    person["posture"], person["box"] = "upright", [.4, .1, .6, .9]
    assert scene._prone_rotation(person, 768, 432) == 0


def test_missing_person_gets_one_rotated_retry_without_inventing_a_body(monkeypatch):
    class Empty:
        def predict(self, frame, **kwargs): return []
    calls = []
    found = {"box": [.2, .6, .8, .95], "posture": "lying", "conf": .85,
             "keypoints": 17, "skeleton": [[.4, .7, .9] for _ in range(17)]}
    def observe(pose, frame, rotation=0):
        calls.append(rotation)
        return [found] if rotation else []
    monkeypatch.setattr(scene, "_pose_people", observe)
    frame = np.zeros((60, 100, 3), dtype=np.uint8)
    result = scene._analyze(frame, Empty(), Empty(), 100, 60, detect_objects=False)
    assert calls == [0, 90]
    assert result["person_count"] == 1 and result["persons"][0]["posture"] == "lying"
    calls.clear()
    monkeypatch.setattr(scene, "_pose_people", lambda *a, **kw: calls.append(kw.get("rotation", 0)) or [])
    result = scene._analyze(frame, Empty(), Empty(), 100, 60, detect_objects=False)
    assert result["person_count"] == 0 and calls == [0, 90]


def test_prone_refinement_keeps_one_complete_better_observation(monkeypatch):
    class Empty:
        def predict(self, frame, **kwargs): return []
    original = {"box": [.2, .6, .8, .95], "posture": "lying", "conf": .8,
                "keypoints": 17, "skeleton": [[.5, .7, .55] for _ in range(17)]}
    for i in (11, 12): original["skeleton"][i] = [.3, .7, .55]
    refined = {**original, "box": [.18, .58, .82, .95], "conf": .9,
               "skeleton": [[.5, .72, .95] for _ in range(17)]}
    calls = []
    def observe(pose, frame, rotation=0):
        calls.append(rotation)
        return [dict(refined if rotation else original)]
    monkeypatch.setattr(scene, "_pose_people", observe)
    result = scene._analyze(np.zeros((60, 100, 3), dtype=np.uint8), Empty(), Empty(), 100, 60)
    assert calls == [0, 270]
    assert result["person_count"] == 1
    assert result["persons"][0]["skeleton"] == refined["skeleton"]
    assert result["persons"][0]["box"] == original["box"]
    # A weaker rotation must not overwrite the original model observation.
    refined["skeleton"] = [[.5, .72, .3] for _ in range(17)]
    result = scene._analyze(np.zeros((60, 100, 3), dtype=np.uint8), Empty(), Empty(), 100, 60)
    assert result["persons"][0]["skeleton"] == original["skeleton"]


def test_refinement_rejects_confident_legs_on_the_rug_outside_the_target(monkeypatch):
    class Empty:
        def predict(self, frame, **kwargs): return []
    target = {"box": [.3, .6, .8, .95], "posture": "lying", "conf": .8,
              "keypoints": 17, "skeleton": [[.6, .7, .55] for _ in range(17)]}
    for i in (11, 12): target["skeleton"][i] = [.4, .7, .55]
    for i in (13, 14, 15, 16): target["skeleton"][i][2] = .2
    refined = {**target, "box": [.05, .58, .8, .95],
               "skeleton": [[.6, .7, .95] for _ in range(17)]}
    for i in (11, 12): refined["skeleton"][i] = [.4, .7, .95]
    for i in (15, 16): refined["skeleton"][i] = [.1, .7, .99]
    monkeypatch.setattr(scene, "_pose_people", lambda *a, **kw: [refined if kw.get("rotation") else target])
    person = scene._analyze(np.zeros((60, 100, 3), dtype=np.uint8), Empty(), Empty(), 100, 60)["persons"][0]
    assert person["box"] == target["box"]
    assert person["skeleton"][5] == refined["skeleton"][5]
    assert person["skeleton"][15] == person["skeleton"][16] == [0, 0, 0]


def test_upright_people_do_not_pay_for_a_rotated_pose_pass(monkeypatch):
    class Empty:
        def predict(self, frame, **kwargs): return []
    calls = []
    person = {"box": [.4, .1, .6, .9], "posture": "upright", "conf": .9,
              "keypoints": 17, "skeleton": [[.5, .4, .9] for _ in range(17)]}
    monkeypatch.setattr(scene, "_pose_people", lambda *a, **kw: calls.append(kw.get("rotation", 0)) or [person])
    result = scene._analyze(np.zeros((60, 100, 3), dtype=np.uint8), Empty(), Empty(), 100, 60)
    assert result["person_count"] == 1 and calls == [0]


def test_unmatched_rotated_pose_cannot_replace_the_target_or_add_a_bystander(monkeypatch):
    class Empty:
        def predict(self, frame, **kwargs): return []
    target = {"box": [.05, .6, .45, .95], "posture": "lying", "conf": .8,
              "keypoints": 17, "skeleton": [[.3, .7, .6] for _ in range(17)]}
    other = {**target, "box": [.6, .6, .95, .95],
             "skeleton": [[.8, .7, .99] for _ in range(17)]}
    monkeypatch.setattr(scene, "_pose_people", lambda *a, **kw: [other if kw.get("rotation") else target])
    result = scene._analyze(np.zeros((60, 100, 3), dtype=np.uint8), Empty(), Empty(), 100, 60)
    assert result["person_count"] == 1 and result["persons"][0]["skeleton"] == target["skeleton"]


def test_a_second_pose_cannot_overwrite_the_first_persons_joints():
    yolo = [{"box": [.1, .1, .7, .9], "conf": .95, "posture": "unknown"}]
    poses = [{"box": box, "conf": .9, "posture": "upright", "keypoints": 17,
              "skeleton": [[x, .4, .9] for _ in range(17)]}
             for box, x in [([.1, .1, .7, .9], .25), ([.15, .15, .65, .85], .55)]]
    result = scene._merge_people(yolo, poses)
    assert len(result) == 2
    assert {p["skeleton"][5][0] for p in result} == {.25, .55}
