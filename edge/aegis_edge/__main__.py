"""CLI: python -m aegis_edge --config config/edge.yaml [--seconds N]"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import config as config_mod
from .config import Config
from .runner import EdgeRunner, configure_logging


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aegis-edge")
    ap.add_argument("--config", default="config/edge.yaml")
    ap.add_argument("--seconds", type=float, default=None,
                    help="stop after N seconds of wall clock (demo use)")
    ap.add_argument("--source", default=None, help="override camera.source")
    ap.add_argument("--fast", action="store_true",
                    help="replay as fast as possible instead of real time")
    ap.add_argument("--no-loop", action="store_true", help="stop at end of file")
    ap.add_argument("--audio", action="store_true",
                    help="enable the microphone loudness gate")
    args = ap.parse_args(argv)
    if args.audio:
        os.environ["AEGIS_AUDIO"] = "on"

    cfg: Config = config_mod.load(args.config)
    if args.source or args.fast or args.no_loop:
        cfg = config_mod.load(args.config)
        cfg = Config(
            **{
                **cfg.__dict__,
                "camera": type(cfg.camera)(
                    **{
                        **cfg.camera.__dict__,
                        "source": args.source or cfg.camera.source,
                        "realtime": False if args.fast else cfg.camera.realtime,
                        "loop": False if args.no_loop else cfg.camera.loop,
                    }
                ),
            }
        )

    configure_logging(cfg.log_level)
    secret = os.environ.get("EDGE_TOKEN", "dev-edge-token")
    runner = EdgeRunner(cfg, secret=secret, max_seconds=args.seconds)
    stats = runner.run()
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
