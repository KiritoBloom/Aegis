"""Draw the Aegis app icons.

A doorway, not a camera. A camera icon would promise surveillance; the product
is one door watched and everything else left alone.

First attempt was an arch, and an arch in isolation reads as a headstone, which
is the single worst association a care product can ship on a home screen. The
fix is an open door: a frame, a leaf swung out of it, and a floor line, so the
shape reads as somewhere a person walks through.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "web" / "public"
PAPER = (246, 243, 237)
INK = (20, 24, 28)
SAGE = (95, 127, 106)


def bar(hinge, angle_deg, length, thickness):
    """Corners of a rotated bar starting at `hinge`, angle in image coords (y down)."""
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)
    nx, ny = -uy, ux
    hx, hy = hinge
    t = thickness / 2
    return [
        (hx + nx * t, hy + ny * t),
        (hx + ux * length + nx * t, hy + uy * length + ny * t),
        (hx + ux * length - nx * t, hy + uy * length - ny * t),
        (hx - nx * t, hy - ny * t),
    ]


def draw(size: int, pad_ratio: float, leaf_deg: float = -52.0) -> Image.Image:
    scale = 4
    s = size * scale
    img = Image.new("RGB", (s, s), PAPER)
    d = ImageDraw.Draw(img)

    stroke = int(s * 0.042)
    # Frame sits left of centre so the swung leaf has room on the right.
    x0, x1 = int(s * 0.26), int(s * 0.58)
    y0, y1 = int(s * 0.20), int(s * 0.74)

    # Jambs and lintel: an opening, not a closed box.
    d.rectangle((x0, y0, x0 + stroke, y1), fill=INK)
    d.rectangle((x1 - stroke, y0, x1, y1), fill=INK)
    d.rectangle((x0, y0, x1, y0 + stroke), fill=INK)

    # The floor, wider than the frame, so the eye reads a threshold.
    d.rectangle((int(s * 0.18), y1, int(s * 0.76), y1 + int(s * 0.028)), fill=INK)

    # The leaf, hinged on the right jamb, swung out towards the viewer.
    leaf = bar((x1 - stroke, y1), leaf_deg, (y1 - y0) * 0.90, stroke)
    d.polygon(leaf, fill=INK)

    # The person, standing in the gap the leaf has opened.
    r = int(s * 0.058)
    cx, cy = (x0 + x1) // 2, int(s * 0.545)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=SAGE)

    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, 0.14).save(OUT / "icon-192.png")
    draw(512, 0.14).save(OUT / "icon-512.png")
    # Maskable icons get cropped to a circle by Android, so keep the art inside
    # the middle 80% or the door loses its frame.
    draw(512, 0.24).save(OUT / "icon-maskable-512.png")
    # Apple ignores the manifest and wants a real file.
    draw(180, 0.14).save(OUT / "apple-touch-icon.png")
    print("wrote", [p.name for p in sorted(OUT.glob("*.png"))])
