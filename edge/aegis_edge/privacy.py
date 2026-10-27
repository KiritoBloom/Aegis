"""Privacy masks, applied at capture.

This runs *before* the ring buffer, not before the upload. A masked pixel never
enters RAM, so it cannot leak through a crash dump, a saved clip, an encoder, or
a model call. That is a stronger claim than "we blur it later", and it is the one
a family can actually check.
"""
from __future__ import annotations

import cv2
import numpy as np

from .config import Zone


class PrivacyMasker:
    def __init__(self, zones: tuple[Zone, ...]) -> None:
        self.zones = tuple(z for z in zones if z.is_exclusion or z.private)
        self._cache: dict[tuple[int, int], list[np.ndarray]] = {}

    @property
    def names(self) -> list[str]:
        return [z.name for z in self.zones]

    def _polys(self, shape: tuple[int, int]) -> list[np.ndarray]:
        if shape not in self._cache:
            h, w = shape
            self._cache[shape] = [
                np.array([[int(x * w), int(y * h)] for x, y in z.points], dtype=np.int32)
                for z in self.zones
            ]
        return self._cache[shape]

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """Returns a copy with private regions flattened. Not in-place: the caller
        keeps no unmasked reference, so there is nothing to accidentally leak."""
        if not self.zones:
            return frame
        out = frame.copy()
        for poly in self._polys(frame.shape[:2]):
            cv2.fillPoly(out, [poly], (0, 0, 0))
        return out


def masked_ratio(frame: np.ndarray, zones: tuple[Zone, ...]) -> float:
    """Fraction of the frame hidden. Surfaced in the UI as proof, not decoration."""
    masker = PrivacyMasker(zones)
    if not masker.zones:
        return 0.0
    h, w = frame.shape[:2]
    total = 0
    for poly in masker._polys((h, w)):
        m = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(m, [poly], 255)
        total += int(np.count_nonzero(m))
    return min(1.0, total / float(h * w))
