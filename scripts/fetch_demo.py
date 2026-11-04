"""Fetch the six exact URFD camera-1 samples used in the local regression.

Source: https://fenix.ur.edu.pl/mkepski/ds/uf.html
Kwolek & Kepski (2014), University of Rzeszow.
CC BY-NC-SA 4.0; intended for non-commercial academic use by the dataset authors.
The clips are downloaded directly from the university and are not included in Git.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://fenix.ur.edu.pl/mkepski/ds/data"
CLIPS = {
    "fall-01": "eb7184265b66c95f2998eb526ece3c83667bfc747bcef805af8e0621c7913849",
    "fall-02": "aaba2c0b7e540c404f0d2da545df342be52de08966d9a7bd136417270b5ef8aa",
    "fall-03": "c033783409b0ca60ac686352e45b4d7fa1467a2402662b19a0e13c55c20e6a65",
    "fall-04": "ed29339120f253ec2031345187d1d8eded3d6a959242e3d0d6cc1b53b90cc905",
    "fall-05": "0b02b8390845ee454f01b10b6829b4e1b338bfb306266a62fea484468c12992a",
    "fall-06": "6a6e0b741061672b20a1fe0c2a430204bca3c86b4c57a161e24df6e2c4c4f566",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    print("URFD / Kwolek & Kepski 2014 / CC BY-NC-SA 4.0\n"
          "Source and terms: https://fenix.ur.edu.pl/mkepski/ds/uf.html", flush=True)
    for name, expected in CLIPS.items():
        target = directory / f"{name}.mp4"
        if not target.exists():
            temporary = target.with_suffix(".download")
            try:
                request = urllib.request.Request(f"{BASE}/{name}-cam1.mp4", headers={"User-Agent": "Aegis-demo-setup"})
                with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
                if digest(temporary) != expected:
                    raise SystemExit(f"Checksum mismatch for {name}; downloaded clip was not installed.")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        if digest(target) != expected:
            raise SystemExit(f"Existing {target} differs from the demo sample; inspect it before replacing it.")
        print(f"Verified {target.name}", flush=True)
    print(f"\nSamples ready in {directory}. In Setup, choose Add lab footage and add fall-06.mp4.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "data/new-footage")
    fetch(parser.parse_args().directory)
