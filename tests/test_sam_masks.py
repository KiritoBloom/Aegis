"""Live SAM masks put height first; decoding must not silently drop to box blur."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / ".vendor", ROOT / "backend"):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app import meta  # noqa: E402

# A real live prefix for a 1280x720 frame: mask width=208, height=513.
LIVE_PREFIX = "513,208,!!!!%@BG"


class _FakeMask:
    def __init__(self, *, encoding: str, payload: str, width: int, height: int) -> None:
        self.encoding = encoding
        self.payload = payload
        self.width = width
        self.height = height


def _install_fake_parser(monkeypatch, decode) -> None:
    module = types.ModuleType("meta_sam_parser")
    module.SegmentationMask = _FakeMask
    module.decode_mask_to_raster = decode
    monkeypatch.setitem(sys.modules, "meta_sam_parser", module)


def test_mask_raster_accepts_the_swapped_orientation(monkeypatch):
    calls = []

    def decode(mask):
        calls.append((mask.width, mask.height))
        if (mask.width, mask.height) != (208, 513):
            raise ValueError("wrong orientation")
        return b"\x01" * (mask.width * mask.height)

    _install_fake_parser(monkeypatch, decode)

    width, height, bits = meta._mask_raster(LIVE_PREFIX)

    assert (width, height) == (208, 513)
    assert len(bits) == 208 * 513 == 106704
    assert (208, 513) in calls


def test_mask_raster_returns_none_when_both_orientations_fail(monkeypatch):
    calls = []

    def decode(mask):
        calls.append((mask.width, mask.height))
        raise ValueError("undecodable")

    _install_fake_parser(monkeypatch, decode)

    assert meta._mask_raster(LIVE_PREFIX) is None
    assert set(calls) == {(513, 208), (208, 513)}
    assert len(calls) == 2


def test_mask_raster_rejects_a_raster_of_the_wrong_length(monkeypatch):
    def decode(mask):
        return b"\x01" * (mask.width * mask.height - 1)

    _install_fake_parser(monkeypatch, decode)

    assert meta._mask_raster(LIVE_PREFIX) is None


@pytest.mark.parametrize("data", [
    "", "garbage", "513,208", "0,208,!!!!", "-1,208,!!!!", None, b"513,208,!!!!",
])
def test_mask_raster_never_raises_on_malformed_input(data):
    assert meta._mask_raster(data) is None


def test_mask_raster_decodes_a_real_height_first_payload():
    pytest.importorskip("meta_sam_parser")
    # Produced by meta-sam-parser 0.1.0's canonical one-bit encoder for a
    # 9-row x 5-column mask; the transposed decode fails inside the codec.
    data = "9,5,!!!!!)NHEs3/n-X"
    expected = bytes(1 if (x + 2 * y) % 3 else 0 for y in range(9) for x in range(5))

    width, height, bits = meta._mask_raster(data)

    assert (width, height) == (5, 9)
    assert bits == expected
