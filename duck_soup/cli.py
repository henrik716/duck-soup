"""Command-line runner: `python -m duck_soup.cli run pipelines/test.yaml`."""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .engine import run_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="duck_soup", description="Run a Duck Soup pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a pipeline YAML")
    run.add_argument("pipeline", help="Path to a pipeline .yaml file")

    check = sub.add_parser("check", help="Validate a pipeline YAML without running")
    check.add_argument("pipeline")

    args = parser.parse_args(argv)
    cfg = load_config(args.pipeline)

    if args.cmd == "check":
        print(f"OK: '{cfg.name}' with {len(cfg.pipelines)} pipeline(s), "
              f"output: '{cfg.output}'")
        return 0

    if args.cmd == "run":
        out_path = run_config(cfg, log=lambda m: print(f"  {m}", flush=True))
        print(f"\nWrote {out_path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
