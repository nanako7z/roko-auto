#!/usr/bin/env python3
"""
Interception-based periodic input runner (Windows).

Legacy entry point — delegates to the roko package.
For the new multi-task platform, use: python -m roko serve

Usage (backward-compatible):
  python interception_runner.py --config config.yaml
  python interception_runner.py --config config.yaml --once
  python interception_runner.py --record output.bin
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Interception periodic input runner")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--record", metavar="OUTPUT",
                        help="Record keyboard/mouse input to a .bin file. Press F12 to stop.")
    args = parser.parse_args()

    if args.record:
        # Delegate to roko record
        sys.argv = ["roko", "record", args.record, "--config", args.config]
        from roko.cli import cmd_record
        record_args = argparse.Namespace(output=args.record, config=args.config)
        cmd_record(record_args)
    else:
        # Delegate to roko run
        from roko.cli import cmd_run
        run_args = argparse.Namespace(task=None, config=args.config, once=args.once)
        cmd_run(run_args)


if __name__ == "__main__":
    main()
