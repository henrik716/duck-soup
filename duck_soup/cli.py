"""Command-line runner: `python -m duck_soup.cli run pipelines/test.yaml`."""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .engine import run_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="duck-soup", description="Run a Duck Soup pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a pipeline YAML")
    run.add_argument("pipeline", help="Path to a pipeline .yaml file")
    run.add_argument(
        "--history-dir",
        help="Project folder whose runs/ keeps this run's record (default: $DUCK_SOUP_ROOT "
        "if set, else the current folder)",
    )
    run.add_argument("--no-history", action="store_true", help="Don't record this run")

    check = sub.add_parser("check", help="Validate a pipeline YAML without running")
    check.add_argument("pipeline")

    serve = sub.add_parser("serve", help="Launch the web editor")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    tutorial = sub.add_parser(
        "tutorial", help="Copy the Pondsworth tutorial data and config into a folder"
    )
    tutorial.add_argument("folder", nargs="?", default=".", help="Project folder (default: current)")
    tutorial.add_argument("--force", action="store_true", help="Overwrite existing tutorial files")

    args = parser.parse_args(argv)

    if args.cmd == "tutorial":
        return _tutorial(args.folder, args.force)

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("duck_soup.web.app:app", host=args.host, port=args.port)
        return 0

    cfg = load_config(args.pipeline)

    if args.cmd == "check":
        print(f"OK: '{cfg.name}' with {len(cfg.pipelines)} pipeline(s), "
              f"output: '{cfg.output}'")
        return 0

    if args.cmd == "run":
        # A native crash in DuckDB then prints at least a Python traceback, not nothing.
        import faulthandler
        faulthandler.enable()
        return _run(cfg, args)

    return 1


def _run(cfg, args) -> int:
    from datetime import datetime, timezone

    log: list[str] = []

    def echo(m: str) -> None:
        log.append(m)
        print(f"  {m}", flush=True)

    written: list[dict] = []
    started = datetime.now(timezone.utc)
    try:
        out_path = run_config(cfg, log=echo, written=written)
    except Exception as e:
        # Recorded, then re-raised: the exit code and traceback stay what schedulers expect.
        if not args.no_history:
            _record(args, cfg, started, written, log, ok=False, error=str(e))
        raise
    print(f"\nWrote {out_path}")
    if not args.no_history:
        _record(args, cfg, started, written, log, ok=True, output=out_path)
    return 0


def _record(args, cfg, started, written, log, *, ok, output=None, error=None) -> None:
    """Record this run in the project's run history (see history.py), for the editor."""
    from datetime import datetime, timezone
    from pathlib import Path

    from . import history
    from .config import dump_config_yaml

    root = Path(args.history_dir).resolve() if args.history_dir else history.default_root()
    history.record_run(
        root, Path(args.pipeline).stem, trigger="cli",
        started=started, finished=datetime.now(timezone.utc), ok=ok,
        output=output, error=error, layers=written, log=log, yaml_text=dump_config_yaml(cfg),
    )


def _tutorial(folder: str, force: bool) -> int:
    import os
    from pathlib import Path

    from .tutorial import CONFIG_NAME, PIPELINES_DIR, install

    try:
        written = install(folder, force=force)
    except FileExistsError as e:
        print(f"Not copying the tutorial: {e}", file=sys.stderr)
        return 1
    for path in written:
        print(f"  wrote {path}")

    dest = Path(folder).resolve()
    # Same default as web/app.py: the editor lists pipelines/ under its project folder.
    editor_root = Path(os.environ.get("DUCK_SOUP_ROOT", Path.home() / "duck-soup")).resolve()
    config = (PIPELINES_DIR / CONFIG_NAME).as_posix()
    print(f"\nPondsworth is ready in {dest}. From that folder:")
    print(f"  duck-soup run {config}    (every tutorial example, one layer each)")
    if dest == editor_root:
        print(f"  duck-soup serve    (then pick '{Path(CONFIG_NAME).stem}' in the editor's load list)")
    else:
        print(f"  To open it in the editor, set DUCK_SOUP_ROOT to {dest} and run duck-soup serve")
        print(f"  from there (the editor's project folder is currently {editor_root}).")
    print("Guide: https://henrik716.github.io/duck-soup/tutorials/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
