"""Pipeline CLI.

    python -m pipeline.run                 # run every stage
    python -m pipeline.run --force-refresh # ignore the 30-day download cache
    python -m pipeline.run --stage hrsa    # run one stage

Stages are registered in ``STAGES`` and run in order. Each one reports its own
progress and records an entry in ``ingest_runs`` so the dashboard can show when
the data was last built and whether it came from cache.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Config, load_config
from app.db import init_db, session_scope
from pipeline import hrsa


@dataclass(frozen=True)
class Stage:
    name: str
    description: str
    run: Callable[[Session, Config, bool, Callable[[str], None]], object]


def _run_hrsa(
    session: Session, config: Config, force_refresh: bool, report: Callable[[str], None]
) -> hrsa.HrsaIngestResult:
    return hrsa.ingest(
        session, config, force_refresh=force_refresh, on_progress=report
    )


STAGES: tuple[Stage, ...] = (
    Stage("hrsa", "Build the FQHC universe from HRSA downloads", _run_hrsa),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.run",
        description="Build the FQHC prospect database from free public sources.",
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=[stage.name for stage in STAGES],
        help="Run only the named stage (repeatable). Defaults to all stages.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-download source files even if the cached copy is still fresh.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (defaults to the project's own).",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress per-step progress output."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    def report(message: str) -> None:
        if not args.quiet:
            print(f"  {message}", flush=True)

    selected = [s for s in STAGES if not args.stage or s.name in args.stage]
    init_db(config)

    failures = 0
    with session_scope(config) as session:
        for stage in selected:
            print(f"[{stage.name}] {stage.description}", flush=True)
            try:
                result = stage.run(session, config, args.force_refresh, report)
            except Exception as exc:
                failures += 1
                print(f"[{stage.name}] FAILED: {exc}", file=sys.stderr, flush=True)
                continue

            for message in getattr(result, "messages", []):
                print(f"  ! {message}", flush=True)
            if getattr(result, "used_cache", False):
                cache_date = getattr(result, "cache_date", None)
                stamp = f" from {cache_date:%Y-%m-%d}" if cache_date else ""
                print(f"  ! Ran on cached data{stamp}", flush=True)
            print(f"[{stage.name}] done", flush=True)

    if failures:
        print(f"\n{failures} stage(s) failed.", file=sys.stderr)
        return 1

    print(f"\nDatabase: {config.database_file}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
