"""Download the two public, versioned YOLO assets used by Aegis.

Run once before monitoring. Existing files are verified, never overwritten.
The weights remain gitignored; no household media or API key is involved.
"""
from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = "https://github.com/ultralytics/assets/releases/download/v8.3.0"
WEIGHTS = {
    "yolov8n.pt": "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36",
    "yolov8n-pose.pt": "c6fa93dd1ee4a2c18c900a45c1d864a1c6f7aba75d84f91648a30b7fb641d212",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for name, expected in WEIGHTS.items():
        target = args.directory / name
        if not target.exists():
            temporary = target.with_suffix(".download")
            try:
                request = urllib.request.Request(f"{RELEASE}/{name}", headers={"User-Agent": "Aegis-weight-setup"})
                with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
                if digest(temporary) != expected:
                    raise SystemExit(f"Checksum mismatch for {name}; downloaded file was not installed.")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        if digest(target) != expected:
            raise SystemExit(f"Existing {target} differs from the pinned asset; inspect it before replacing it.")
        print(f"Verified {name}: sha256={expected}")


if __name__ == "__main__":
    main()
