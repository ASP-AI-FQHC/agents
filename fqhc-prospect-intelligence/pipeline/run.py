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
from app.models import RunStatus
from pipeline import changes, grants, hrsa, irs, matching, scoring, uds, website
from pipeline.propublica import ProPublicaClient, enrich_financials


@dataclass(frozen=True)
class StageOptions:
    """Per-run switches passed to every stage."""

    force_refresh: bool = False
    # Cap on organizations processed by the API-bound stages. Used for a quick
    # trial run before committing to a full pass; ignored by hrsa and scoring,
    # which are local and fast.
    limit: int | None = None


@dataclass(frozen=True)
class Stage:
    name: str
    description: str
    run: Callable[[Session, Config, StageOptions, Callable[[str], None]], object]
    # Whether --limit means anything for this stage, so the CLI can say when it
    # is being ignored rather than silently doing nothing.
    honours_limit: bool = False


def _run_hrsa(
    session: Session, config: Config, options: StageOptions, report: Callable[[str], None]
) -> hrsa.HrsaIngestResult:
    return hrsa.ingest(
        session, config, force_refresh=options.force_refresh, on_progress=report
    )


def _run_matching(
    session: Session, config: Config, options: StageOptions, report: Callable[[str], None]
) -> matching.MatchingResult:
    with ProPublicaClient(config, session) as client:
        return matching.match_organizations(
            session,
            config,
            client=client,
            force=options.force_refresh,
            limit=options.limit,
            on_progress=report,
        )


def _run_financials(
    session: Session, config: Config, options: StageOptions, report: Callable[[str], None]
) -> object:
    with ProPublicaClient(config, session) as client:
        return enrich_financials(
            session,
            config,
            client=client,
            force=options.force_refresh,
            limit=options.limit,
            on_progress=report,
        )


def _run_people(
    session: Session, config: Config, options: StageOptions, report: Callable[[str], None]
) -> irs.PeopleResult:
    import httpx

    with httpx.Client(follow_redirects=True) as client:
        return irs.enrich_people(
            session,
            config,
            client=client if config.irs.fetch_remote else None,
            limit=options.limit,
            on_progress=report,
        )


def _run_uds(
    session: Session, config: Config, _options: StageOptions, report: Callable[[str], None]
) -> uds.UdsResult:
    return uds.ingest(session, config, on_progress=report)


def _run_grants(
    session: Session, config: Config, _options: StageOptions, report: Callable[[str], None]
) -> grants.GrantResult:
    return grants.ingest(session, config, on_progress=report)


def _run_websites(
    session: Session, config: Config, options: StageOptions, report: Callable[[str], None]
) -> website.WebsiteResult:
    return website.enrich_websites(
        session,
        config,
        limit=options.limit,
        force=options.force_refresh,
        on_progress=report,
    )


def _run_scoring(
    session: Session, config: Config, _options: StageOptions, report: Callable[[str], None]
) -> scoring.ScoringResult:
    return scoring.score_all(session, config, on_progress=report)


def _run_changes(
    session: Session, config: Config, _options: StageOptions, report: Callable[[str], None]
) -> changes.ChangeResult:
    return changes.detect_changes(session, config, on_progress=report)


STAGES: tuple[Stage, ...] = (
    Stage("hrsa", "Build the FQHC universe from HRSA downloads", _run_hrsa),
    Stage("ein", "Resolve EINs via ProPublica search", _run_matching, honours_limit=True),
    Stage(
        "financials", "Pull Form 990 filings by EIN", _run_financials, honours_limit=True
    ),
    Stage(
        "people", "Read Form 990 Part VII people and contractors", _run_people,
        honours_limit=True,
    ),
    Stage("uds", "Load HRSA UDS patients, staffing and payer mix", _run_uds),
    Stage("grants", "Load awarded grants from award files and Schedule I", _run_grants),
    Stage(
        "website",
        "Read leadership pages for organizations with no Part VII people",
        _run_websites,
        honours_limit=True,
    ),
    Stage("scoring", "Score every organization against the ICP", _run_scoring),
    Stage("changes", "Record what moved since the last run", _run_changes),
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
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Process at most N organizations in the API-bound stages (ein, "
            "financials, people, website). Use for a quick trial run before "
            "committing to a full pass; the result is deliberately partial."
        ),
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress per-step progress output."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    config = load_config(args.config)
    options = StageOptions(force_refresh=args.force_refresh, limit=args.limit)

    def report(message: str) -> None:
        if not args.quiet:
            print(f"  {message}", flush=True)

    selected = [s for s in STAGES if not args.stage or s.name in args.stage]
    init_db(config)

    # Always stated. A run that writes to a different database than the one the
    # window opens looks exactly like a run that found nothing.
    print(f"Database: {config.database_file}", flush=True)

    if options.limit is not None:
        limited = [s.name for s in selected if s.honours_limit]
        ignored = [s.name for s in selected if not s.honours_limit]
        print(
            f"TRIAL RUN: at most {options.limit} organizations in "
            f"{', '.join(limited) if limited else 'no selected stage'}"
            + (f" (--limit does not apply to {', '.join(ignored)})" if ignored else ""),
            flush=True,
        )

    failures = 0
    with session_scope(config) as session:
        for stage in selected:
            print(f"[{stage.name}] {stage.description}", flush=True)
            try:
                result = stage.run(session, config, options, report)
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

            # A stage that completed on stale data, or not at all, must not
            # report the same "done" as a clean live run.
            status = getattr(result, "status", None)
            if status is RunStatus.FAILED:
                failures += 1
            suffix = "" if status in (None, RunStatus.SUCCESS) else f" ({status.value})"
            print(f"[{stage.name}] done{suffix}", flush=True)

    if failures:
        print(f"\n{failures} stage(s) failed.", file=sys.stderr)
        return 1

    print(f"\nDatabase: {config.database_file}")
    if options.limit is not None:
        # A capped run must never be mistaken for a complete one.
        print(
            f"This was a trial run capped at {options.limit} organizations. "
            "Re-run without --limit for the full set."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
