"""Capture-time privacy masks. Masked pixels never enter the ring buffer."""
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
