"""Resolve each FQHC organization to an IRS EIN via ProPublica search.

The governing rule is that the pipeline never silently guesses. Fuzzy name
similarity produces a 0-100 confidence score, and that score routes the match:

* ``>= auto_accept_score`` (default 90) -- accepted automatically
* ``review_score`` .. ``auto_accept_score`` (default 70-89) -- queued for a
  human in the review UI
* ``< review_score`` -- left unmatched and flagged

Two additional safeguards apply above the auto-accept line:

* If the two best candidates are near-identical in score, the match is sent to
  review rather than auto-accepted -- a high score on both means the name alone
  cannot distinguish them.
* A candidate whose state disagrees with HRSA's is discarded outright (unless
  ``require_state_match`` is off).

The displayed confidence is pure name similarity. City agreement and filing
history only break ties, so the number a human sees is never inflated by
factors they cannot see.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Config, MatchingSettings
from app.models import (
    EinMatch,
    IngestRun,
    MatchStatus,
    Organization,
    RunStatus,
    utcnow,
)
from pipeline.propublica import (
    ProPublicaClient,
    ProPublicaUnavailable,
    SearchHit,
    normalize_ein,
)
from pipeline.text import normalize_name, normalize_state

ProgressFn = Callable[[str], None]

# How many runner-up candidates to keep for the review UI.
MAX_STORED_CANDIDATES = 5

# Give up on the stage after this many organizations in a row fail outright,
# rather than issuing hundreds of doomed requests.
CONSECUTIVE_FAILURE_LIMIT = 3


def name_similarity(left: str | None, right: str | None) -> float:
    """0-100 similarity between two organization names.

    ``token_sort_ratio`` on normalized names: word order and punctuation do not
    matter, but extra or missing words legitimately cost points. It is used
    rather than ``token_set_ratio`` because the latter scores
    "Erie Family Health" against "Erie Family Health Network of Illinois" as a
    perfect match, which is exactly the false confidence this module avoids.
    """
    a, b = normalize_name(left), normalize_name(right)
    if not a or not b:
        return 0.0
    return float(fuzz.token_sort_ratio(a, b))


@dataclass(frozen=True)
class ScoredCandidate:
    ein: str
    name: str
    city: str | None
    state: str | None
    score: float
    state_match: bool
    city_match: bool
    has_filings: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "ein": self.ein,
            "name": self.name,
            "city": self.city,
            "state": self.state,
            "score": round(self.score, 1),
            "state_match": self.state_match,
            "city_match": self.city_match,
            "has_filings": self.has_filings,
        }


@dataclass
class MatchOutcome:
    """The decision for one organization, ready to persist."""

    status: MatchStatus
    ein: str | None = None
    matched_name: str | None = None
    matched_city: str | None = None
    matched_state: str | None = None
    score: float | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None


def score_candidates(
    org_name: str,
    org_state: str | None,
    org_city: str | None,
    hits: list[SearchHit],
    settings: MatchingSettings,
    *,
    excluded_eins: set[str] | None = None,
) -> list[ScoredCandidate]:
    """Score and rank search hits. Returns best first."""
    excluded = excluded_eins or set()
    target_state = normalize_state(org_state)
    target_city = (org_city or "").strip().lower()

    scored: list[ScoredCandidate] = []
    for hit in hits:
        ein = normalize_ein(hit.ein)
        if not ein or ein in excluded:
            continue

        hit_state = normalize_state(hit.state)
        state_match = bool(target_state and hit_state and hit_state == target_state)
        if settings.require_state_match and target_state and not state_match:
            continue

        scored.append(
            ScoredCandidate(
                ein=ein,
                name=hit.name,
                city=hit.city,
                state=hit_state,
                score=name_similarity(org_name, hit.name),
                state_match=state_match,
                city_match=bool(
                    target_city and hit.city and hit.city.strip().lower() == target_city
                ),
                has_filings=bool(hit.raw.get("have_filings")),
            )
        )

    # Name score decides; city agreement and filing history only break ties.
    scored.sort(
        key=lambda c: (c.score, c.city_match, c.has_filings, c.state_match),
        reverse=True,
    )
    return scored


def decide(
    candidates: list[ScoredCandidate],
    settings: MatchingSettings,
    *,
    searched_terms: str = "",
    had_hits: bool = True,
) -> MatchOutcome:
    """Route ranked candidates into auto / review / unmatched."""
    stored = [c.as_dict() for c in candidates[:MAX_STORED_CANDIDATES]]

    if not candidates:
        note = (
            "No ProPublica results for this name in the organization's state"
            if had_hits
            else f"ProPublica returned no results for {searched_terms or 'this name'}"
        )
        return MatchOutcome(status=MatchStatus.UNMATCHED, note=note, candidates=stored)

    best = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None

    base = MatchOutcome(
        status=MatchStatus.UNMATCHED,
        ein=best.ein,
        matched_name=best.name,
        matched_city=best.city,
        matched_state=best.state,
        score=best.score,
        candidates=stored,
    )

    if best.score < settings.review_score:
        base.status = MatchStatus.UNMATCHED
        base.note = (
            f"Best candidate scored {best.score:.0f}, below the "
            f"{settings.review_score:.0f} review threshold"
        )
        # Below the review line the EIN is not a claim, just a breadcrumb.
        base.ein = None
        base.matched_name = best.name
        return base

    if best.score >= settings.auto_accept_score:
        if (
            runner_up is not None
            and runner_up.ein != best.ein
            and best.score - runner_up.score <= settings.ambiguity_margin
        ):
            base.status = MatchStatus.PENDING
            base.note = (
                f"Two candidates scored within {settings.ambiguity_margin:.0f} points "
                f"({best.score:.0f} vs {runner_up.score:.0f}); name alone cannot "
                "distinguish them"
            )
            return base
        base.status = MatchStatus.AUTO
        base.note = f"Auto-accepted at {best.score:.0f}"
        return base

    base.status = MatchStatus.PENDING
    base.note = (
        f"Scored {best.score:.0f}, between the {settings.review_score:.0f} review "
        f"and {settings.auto_accept_score:.0f} auto-accept thresholds"
    )
    return base


def query_variants(name: str) -> list[str]:
    """Search terms to try, in order, stopping at the first with results.

    Long punctuated legal names sometimes return nothing, so a normalized form
    and a shortened head-of-name form are tried as fallbacks.
    """
    variants = [name.strip()]
    normalized = normalize_name(name)
    if normalized and normalized not in {v.lower() for v in variants}:
        variants.append(normalized)

    tokens = normalized.split()
    if len(tokens) > 4:
        variants.append(" ".join(tokens[:4]))
    return [v for v in variants if v]


def should_search(match: EinMatch | None, *, force: bool, refresh_after_days: int) -> bool:
    """Whether to (re)search this organization.

    Human decisions are never re-litigated: an accepted match is final, and a
    rejected one is only re-searched to look for a *different* candidate.
    """
    if match is None:
        return True
    status = MatchStatus(match.status)

    if status is MatchStatus.ACCEPTED:
        return False
    if status in (MatchStatus.AUTO, MatchStatus.PENDING):
        # AUTO is settled; PENDING is waiting on a human. Neither should churn.
        return force
    if force:
        return True

    # UNMATCHED / REJECTED: try again once the search result is stale, in case
    # ProPublica has since added the organization.
    if match.searched_at is None:
        return True
    age = utcnow() - _as_utc(match.searched_at)
    return age >= timedelta(days=refresh_after_days)


@dataclass
class MatchingResult:
    examined: int = 0
    searched: int = 0
    skipped: int = 0
    auto_accepted: int = 0
    needs_review: int = 0
    unmatched: int = 0
    failed: int = 0
    # Organizations not searched because they sit outside the scoring
    # footprint. Reported so a low match rate is never mistaken for a problem.
    out_of_footprint: int = 0
    used_cache: bool = False
    source_reachable: bool = True
    messages: list[str] = field(default_factory=list)

    @property
    def status(self) -> RunStatus:
        if self.searched == 0 and self.failed > 0:
            return RunStatus.FAILED
        return RunStatus.PARTIAL if not self.source_reachable else RunStatus.SUCCESS


def match_organizations(
    session: Session,
    config: Config,
    *,
    client: ProPublicaClient,
    force: bool = False,
    limit: int | None = None,
    on_progress: ProgressFn | None = None,
) -> MatchingResult:
    """Resolve EINs for every organization that needs one."""
    report = on_progress or (lambda _message: None)
    settings = config.matching
    result = MatchingResult()

    run = IngestRun(stage="ein_matching", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    statement = select(Organization).order_by(Organization.name)
    footprint = config.api_states
    if footprint:
        statement = statement.where(Organization.state.in_(footprint))

    organizations = session.scalars(statement).all()
    if footprint:
        total = session.scalar(select(func.count()).select_from(Organization)) or 0
        result.out_of_footprint = max(total - len(organizations), 0)
        if result.out_of_footprint:
            report(
                f"Searching {len(organizations):,} organizations in "
                f"{', '.join(footprint)}; skipping {result.out_of_footprint:,} "
                "outside the footprint"
            )
    consecutive_failures = 0

    try:
        for index, org in enumerate(organizations, start=1):
            if limit is not None and result.searched >= limit:
                break

            result.examined += 1
            match = org.ein_match

            if not should_search(
                match, force=force, refresh_after_days=settings.refresh_after_days
            ):
                result.skipped += 1
                continue

            rejected = set(match.rejected_eins or []) if match else set()

            try:
                candidates, from_cache, had_hits, terms = _search_candidates(
                    client, org, settings, rejected
                )
            except ProPublicaUnavailable as exc:
                result.failed += 1
                result.source_reachable = False
                consecutive_failures += 1
                # Say so immediately: with the full retry ladder a failing
                # search takes a minute, and silence looks like a hang.
                report(f"ProPublica unreachable while searching {org.name}: {exc}")
                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    result.messages.append(
                        f"ProPublica unreachable ({exc}); stopped after "
                        f"{consecutive_failures} consecutive failures with "
                        f"{result.searched} of {len(organizations)} organizations searched"
                    )
                    break
                continue

            consecutive_failures = 0
            result.searched += 1
            result.used_cache = result.used_cache or from_cache

            outcome = decide(
                candidates, settings, searched_terms=terms, had_hits=had_hits
            )
            _persist_outcome(session, org, match, outcome)

            if outcome.status is MatchStatus.AUTO:
                result.auto_accepted += 1
            elif outcome.status is MatchStatus.PENDING:
                result.needs_review += 1
            else:
                result.unmatched += 1

            if index % 50 == 0:
                report(
                    f"Searched {result.searched:,} organizations "
                    f"({result.auto_accepted:,} auto, {result.needs_review:,} to review)"
                )

        session.commit()
    except Exception as exc:
        session.rollback()
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.message = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise

    if client.stale_responses:
        result.used_cache = True
        result.source_reachable = False
        result.messages.append(
            f"{client.stale_responses} search response(s) served from an expired "
            "cache because ProPublica was unreachable"
        )

    if result.out_of_footprint:
        result.messages.append(
            f"{result.out_of_footprint:,} organizations outside "
            f"{', '.join(footprint or [])} were not searched "
            "(pipeline.restrict_api_to_target_states)"
        )

    report(
        f"Matched {result.auto_accepted:,} automatically, "
        f"{result.needs_review:,} need review, {result.unmatched:,} unmatched"
    )

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.examined
    run.records_written = result.searched
    run.used_cache = result.used_cache
    run.source_reachable = result.source_reachable
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result


def _search_candidates(
    client: ProPublicaClient,
    org: Organization,
    settings: MatchingSettings,
    rejected: set[str],
) -> tuple[list[ScoredCandidate], bool, bool, str]:
    """Run the search variants for one organization.

    Returns (candidates, served_from_cache, any_raw_hits, terms_tried).
    """
    state = org.state if settings.require_state_match else None
    from_cache = False
    had_hits = False
    tried: list[str] = []

    for term in query_variants(org.name):
        tried.append(term)
        hits, api_result = client.search(term, state)
        from_cache = from_cache or api_result.from_cache
        if not hits:
            continue

        had_hits = True
        candidates = score_candidates(
            org.name,
            org.state,
            org.city,
            hits[: settings.max_candidates],
            settings,
            excluded_eins=rejected,
        )
        if candidates:
            return candidates, from_cache, had_hits, ", ".join(tried)

    return [], from_cache, had_hits, ", ".join(tried)


def _persist_outcome(
    session: Session,
    org: Organization,
    match: EinMatch | None,
    outcome: MatchOutcome,
) -> None:
    if match is None:
        match = EinMatch(organization_id=org.id)
        session.add(match)

    match.ein = outcome.ein
    match.matched_name = outcome.matched_name
    match.matched_city = outcome.matched_city
    match.matched_state = outcome.matched_state
    match.score = outcome.score
    match.status = outcome.status
    match.candidates = outcome.candidates
    match.note = outcome.note
    match.searched_at = utcnow()
    # A fresh search supersedes any earlier automated decision, but human
    # decisions never reach this function (should_search filters them out).
    match.decided_at = None
    match.decided_by = None
    session.flush()


def _as_utc(value):
    from datetime import timezone

    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
