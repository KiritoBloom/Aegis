"""Zone geometry and trajectory features.

Pure functions over normalized (0..1) coordinates. No OpenCV, no numpy, no I/O,
so every rule in this module is unit-testable without a camera.

Coordinate convention: (x, y) where x grows right and y grows DOWN, matching
image space. A "foot point" is (box centre x, box bottom y) — the point that
touches the floor and therefore the point that crosses a door threshold.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

Point = tuple[float, float]


def point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    """Ray casting. Points exactly on an edge may fall either way; callers
    apply hysteresis, so an edge case never flaps an alert on its own."""
    x, y = point
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _seg_distance(p: Point, a: Point, b: Point) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_polygon(point: Point, polygon: tuple[Point, ...]) -> float:
    """0 inside the zone, otherwise the shortest distance to its boundary."""
    if point_in_polygon(point, polygon):
        return 0.0
    n = len(polygon)
    return min(_seg_distance(point, polygon[i], polygon[(i + 1) % n]) for i in range(n))


def polygon_centroid(polygon: tuple[Point, ...]) -> Point:
    n = len(polygon)
    return (sum(p[0] for p in polygon) / n, sum(p[1] for p in polygon) / n)


def bbox_overlap_ratio(box: tuple[float, float, float, float], polygon: tuple[Point, ...]) -> float:
    """Fraction of a normalized box's area that falls inside the polygon.

    Sampled on a 5x5 grid — cheap, and precise enough for a hazard test.
    """
    x1, y1, x2, y2 = box
    hits = 0
    for i in range(5):
        for j in range(5):
            px = x1 + (x2 - x1) * (i + 0.5) / 5
            py = y1 + (y2 - y1) * (j + 0.5) / 5
            if point_in_polygon((px, py), polygon):
                hits += 1
    return hits / 25.0


@dataclass
class Sample:
    t: float
    x: float
    y: float
    w: float = 0.0
    h: float = 0.0
    conf: float = 1.0


@dataclass
class TrackHistory:
    """Rolling foot-point history for one track ID. Memory only, last N samples."""

    maxlen: int = 120
    samples: deque[Sample] = field(default_factory=lambda: deque(maxlen=120))

    def __post_init__(self) -> None:
        if self.samples.maxlen != self.maxlen:
            self.samples = deque(self.samples, maxlen=self.maxlen)

    def add(self, t: float, x: float, y: float, w: float = 0.0, h: float = 0.0, conf: float = 1.0) -> None:
        self.samples.append(Sample(t, x, y, w, h, conf))

    def __len__(self) -> int:
        return len(self.samples)

    @property
    def last(self) -> Sample | None:
        return self.samples[-1] if self.samples else None

    def span_s(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1].t - self.samples[0].t

    # ---- zone features -------------------------------------------------

    def dwell_in(self, polygon: tuple[Point, ...]) -> float:
        """Seconds spent inside the polygon, counting only contiguous time."""
        total = 0.0
        prev = None
        for s in self.samples:
            inside = point_in_polygon((s.x, s.y), polygon)
            if inside and prev is not None:
                total += max(0.0, s.t - prev.t)
            prev = s
        return total

    def ever_inside(self, polygon: tuple[Point, ...]) -> bool:
        return any(point_in_polygon((s.x, s.y), polygon) for s in self.samples)

    def entries(self, polygon: tuple[Point, ...]) -> int:
        """Number of outside->inside transitions. Two or more means wandering."""
        count = 0
        prev_inside = False
        for s in self.samples:
            inside = point_in_polygon((s.x, s.y), polygon)
            if inside and not prev_inside:
                count += 1
            prev_inside = inside
        return count

    def min_distance(self, polygon: tuple[Point, ...]) -> float:
        if not self.samples:
            return 1.0
        return min(distance_to_polygon((s.x, s.y), polygon) for s in self.samples)

    def approach_speed(self, polygon: tuple[Point, ...], window_s: float = 1.0) -> float:
        """Positive means closing on the zone. Normalized units per second."""
        if len(self.samples) < 2:
            return 0.0
        now = self.samples[-1]
        d_now = distance_to_polygon((now.x, now.y), polygon)
        ref = None
        for s in reversed(self.samples):
            if now.t - s.t >= window_s:
                ref = s
                break
        if ref is None:
            return 0.0
        dt = now.t - ref.t
        if dt <= 1e-6:
            return 0.0
        d_then = distance_to_polygon((ref.x, ref.y), polygon)
        return max(0.0, (d_then - d_now) / dt)

    def path_length(self) -> float:
        return sum(
            math.hypot(b.x - a.x, b.y - a.y)
            for a, b in zip(list(self.samples)[:-1], list(self.samples)[1:])
        )

    def displacement(self, window_s: float = 1.0) -> float:
        """Straight-line movement over the last window. Feeds motion energy."""
        if len(self.samples) < 2:
            return 0.0
        now = self.samples[-1]
        for s in reversed(self.samples):
            if now.t - s.t >= window_s:
                return math.hypot(now.x - s.x, now.y - s.y)
        ref = self.samples[0]
        return math.hypot(now.x - ref.x, now.y - ref.y)

    def direction_changes(self, min_angle_deg: float = 90.0) -> int:
        """Turns sharper than `min_angle_deg`. Wandering scores high; a fall scores ~0."""
        pts = [(s.x, s.y) for s in self.samples]
        if len(pts) < 4:
            return 0
        changes = 0
        prev_ang = None
        for a, b in zip(pts[:-1], pts[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            if math.hypot(dx, dy) < 1e-4:
                continue
            ang = math.degrees(math.atan2(dy, dx))
            if prev_ang is not None:
                diff = abs((ang - prev_ang + 180) % 360 - 180)
                if diff > min_angle_deg:
                    changes += 1
            prev_ang = ang
        return changes

    def to_features(self, polygon: tuple[Point, ...] | None = None) -> dict:
        out = {
            "frames_present": len(self.samples),
            "present_s": round(self.span_s(), 2),
            "path_length": round(self.path_length(), 3),
            "direction_changes": self.direction_changes(),
            "motion_energy": round(self.displacement(1.0), 3),
            "last_foot": None,
        }
        if self.samples:
            s = self.samples[-1]
            out["last_foot"] = [round(s.x, 4), round(s.y, 4)]
            out["track_confidence"] = round(
                sum(x.conf for x in self.samples) / len(self.samples), 3
            )
        if polygon is not None:
            out.update(
                {
                    "dwell_in_zone_s": round(self.dwell_in(polygon), 2),
                    "min_distance_to_zone": round(self.min_distance(polygon), 4),
                    "approach_speed": round(self.approach_speed(polygon), 4),
                    "zone_entries": self.entries(polygon),
                    "inside_zone": bool(
                        self.samples and point_in_polygon((self.samples[-1].x, self.samples[-1].y), polygon)
                    ),
                }
            )
        return out


def posture_from_keypoints(kp: list[tuple[float, float, float]] | None) -> str:
    """Classify posture from 17 COCO keypoints [(x, y, conf), ...].

    Returns upright | sitting | lying | kneeling | bending | unknown.
    Conservative by design: `unknown` never raises an alert on its own.
    """
    if not kp or len(kp) < 17:
        return "unknown"
    # COCO indices: 5/6 shoulders, 11/12 hips, 13/14 knees, 15/16 ankles, 0 nose
    ls, rs, lh, rh = kp[5], kp[6], kp[11], kp[12]
    lk, rk = kp[13], kp[14]
    visible = [p for p in (ls, rs, lh, rh) if p[2] >= 0.3]
    if len(visible) < 3:
        return "unknown"

    sh = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    hp = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    dx, dy = hp[0] - sh[0], hp[1] - sh[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return "unknown"
    # y grows DOWN, so an upright body has dy > 0 and therefore angle ~0.
    # (atan2(dx, -dy) is the same mistake in sign and reports 180 for upright.)
    torso_angle = abs(math.degrees(math.atan2(dx, dy)))  # 0 = upright, 90 = horizontal

    knees_visible = lk[2] >= 0.3 or rk[2] >= 0.3
    knee_y = (lk[1] + rk[1]) / 2 if knees_visible else None

    if torso_angle > 70:
        return "lying"
    if torso_angle > 45:
        # bending keeps the hips above the knees; sitting puts them at knee height
        if knee_y is not None and hp[1] > knee_y - 0.02:
            return "sitting"
        return "bending"
    if knee_y is not None and knee_y > hp[1] and hp[1] > sh[1]:
        # hips below shoulders and knees below hips => crouched/kneeling low
        if hp[1] - knee_y > -0.02 and torso_angle > 25:
            return "kneeling"
    return "upright"
