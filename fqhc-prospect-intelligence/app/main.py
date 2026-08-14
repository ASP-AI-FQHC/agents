"""FastAPI application: the branded FQHC prospect dashboard.

Run with::

    uvicorn app.main:app

Pages are server-rendered Jinja2. Interactivity (live filtering, review
decisions, refresh progress) is a small amount of fetch-based JavaScript in
``static/js/app.js`` that re-requests server-rendered fragments -- no build
step, no CDN dependency, and every page works without JavaScript as a plain
form submission.
"""

from __future__ import annotations

import math
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import exports, formatting
from app.config import get_config
from app.db import get_db, init_db
from app.models import ChangeEvent, ChangeKind, GranteeType, MatchStatus, utcnow
from app import ntee
from app.queries import (
    MATCH_FILTERS,
    Filters,
    data_status,
    fetch_rows,
    organization_changes,
    organization_contractors,
    organization_detail,
    organization_people,
    organization_website_crawl,
    organization_website_people,
    review_queue,
    similar_organizations,
    summarize,
)
from app.refresh import manager
from pipeline.changes import recent_changes
from pipeline.propublica import format_ein

BASE_DIR = Path(__file__).resolve().parent
config = get_config()

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(
    config=config,
    # True in a packaged desktop build, where telling the user to run a CLI
    # command would be useless -- they have a Refresh button instead.
    is_packaged=bool(getattr(sys, "frozen", False)),
    MatchStatus=MatchStatus,
    GranteeType=GranteeType,
    match_filters=list(MATCH_FILTERS),
)
templates.env.filters.update(
    money=formatting.money,
    number=formatting.number,
    percent=formatting.percent,
    na=formatting.text,
    ein=format_ein,
    ntee=ntee.label,
    age=formatting.age_label,
    months_since=formatting.months_since,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(config)
    yield


app = FastAPI(title=config.app.name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Request plumbing
# ---------------------------------------------------------------------------


def get_filters(
    q: str | None = Query(None),
    state: list[str] = Query(default=[]),
    min_score: float | None = Query(None),
    min_sites: int | None = Query(None),
    min_revenue: float | None = Query(None),
    max_revenue: float | None = Query(None),
    match: str | None = Query(None),
    grantee_type: str | None = Query(None),
    sort: str = Query("score"),
    direction: str = Query("desc"),
    page: int = Query(1, ge=1),
) -> Filters:
    return Filters(
        q=q,
        states=list(state),
        min_score=min_score,
        min_sites=min_sites,
        min_revenue=min_revenue,
        max_revenue=max_revenue,
        match=match,
        grantee_type=grantee_type,
        sort=sort,
        direction=direction,
        page=page,
    ).normalized()


def page_context(session: Session, request: Request) -> dict:
    """Values every page needs: freshness banner and review-queue badge."""
    status = data_status(session)
    from sqlalchemy import func, select

    return {
        "request": request,
        "status": status,
        # Shown in the empty state: an app pointed at the wrong database looks
        # identical to one whose pipeline has never run.
        "database_file": config.database_file,
        "review_count": summarize(session).needs_review,
        "change_count": session.scalar(
            select(func.count()).select_from(ChangeEvent)
        )
        or 0,
        "refresh": manager.state,
        "now": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Session = Depends(get_db),
    filters: Filters = Depends(get_filters),
):
    page_size = config.ui.page_size
    rows, total = fetch_rows(session, filters, page_size=page_size)
    summary = summarize(session)
    top_rows, _ = fetch_rows(session, Filters(sort="score", direction="desc"), limit=10)

    context = page_context(session, request)
    context.update(
        rows=rows,
        total=total,
        summary=summary,
        top_rows=top_rows,
        filters=filters,
        page_count=max(math.ceil(total / page_size), 1) if total else 1,
        all_states=_distinct_states(session),
    )
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/table", response_class=HTMLResponse)
def table_fragment(
    request: Request,
    session: Session = Depends(get_db),
    filters: Filters = Depends(get_filters),
):
    """The master table on its own, for live filtering without a full reload."""
    page_size = config.ui.page_size
    rows, total = fetch_rows(session, filters, page_size=page_size)
    return templates.TemplateResponse(
        request,
        "partials/table.html",
        {
            "request": request,
            "rows": rows,
            "total": total,
            "filters": filters,
            "page_count": max(math.ceil(total / page_size), 1) if total else 1,
        },
    )


@app.get("/organizations/{organization_id}", response_class=HTMLResponse)
def organization_page(
    organization_id: int,
    request: Request,
    session: Session = Depends(get_db),
):
    organization, score, match, filings = organization_detail(session, organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    context = page_context(session, request)
    context.update(
        organization=organization,
        score=score,
        match=match,
        filings=filings,
        stale_months=config.ui.filing_stale_months,
        ntee_specific=ntee.describe(organization.ntee_code)[0],
        ntee_group=ntee.describe(organization.ntee_code)[1],
        similar=similar_organizations(session, organization),
        history=organization_changes(session, organization.id),
        people=organization_people(session, organization.ein),
        contractors=organization_contractors(session, organization.ein),
        website_people=organization_website_people(session, organization.id),
        website_crawl=organization_website_crawl(session, organization.id),
    )
    return templates.TemplateResponse(request, "detail.html", context)


@app.get("/changes", response_class=HTMLResponse)
def changes_page(
    request: Request,
    kind: str | None = Query(None),
    session: Session = Depends(get_db),
):
    from sqlalchemy import func, select

    valid = {k.value for k in ChangeKind}
    selected = kind if kind in valid else None

    counts = dict(
        session.execute(
            select(ChangeEvent.kind, func.count()).group_by(ChangeEvent.kind)
        ).all()
    )
    context = page_context(session, request)
    context.update(
        changes=recent_changes(session, kind=selected),
        selected_kind=selected,
        kind_counts=[
            (ChangeKind(value), count)
            for value, count in sorted(counts.items())
            if count
        ],
        # No events yet may mean nothing moved, or may mean only the baseline
        # run has happened. Those read very differently to a user.
        baseline_only=not counts and _changes_run_count(session) <= 1,
    )
    return templates.TemplateResponse(request, "changes.html", context)


def _changes_run_count(session: Session) -> int:
    from sqlalchemy import func, select

    from app.models import IngestRun

    return (
        session.scalar(
            select(func.count())
            .select_from(IngestRun)
            .where(IngestRun.stage == "changes", IngestRun.finished_at.is_not(None))
        )
        or 0
    )


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request, session: Session = Depends(get_db)):
    context = page_context(session, request)
    context.update(queue=review_queue(session))
    return templates.TemplateResponse(request, "review.html", context)


# ---------------------------------------------------------------------------
# Review decisions
# ---------------------------------------------------------------------------


@app.post("/review/{organization_id}/accept")
def accept_match(
    organization_id: int,
    request: Request,
    ein: str = Form(...),
    decided_by: str = Form("dashboard user"),
    session: Session = Depends(get_db),
):
    organization, _, match, _ = organization_detail(session, organization_id)
    if organization is None or match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    # Accepting a runner-up is allowed: the review UI offers alternatives, and
    # the chosen EIN becomes the confirmed one.
    candidate = _find_candidate(match, ein)
    match.ein = ein
    if candidate:
        match.matched_name = candidate.get("name")
        match.matched_city = candidate.get("city")
        match.matched_state = candidate.get("state")
        match.score = candidate.get("score", match.score)
    match.status = MatchStatus.ACCEPTED
    match.decided_at = utcnow()
    match.decided_by = decided_by
    session.commit()

    return _after_decision(request, session, organization_id, "accepted")


@app.post("/review/{organization_id}/reject")
def reject_match(
    organization_id: int,
    request: Request,
    ein: str = Form(""),
    decided_by: str = Form("dashboard user"),
    session: Session = Depends(get_db),
):
    organization, _, match, _ = organization_detail(session, organization_id)
    if organization is None or match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    rejected = list(match.rejected_eins or [])
    candidate_ein = ein or match.ein
    if candidate_ein and candidate_ein not in rejected:
        rejected.append(candidate_ein)

    match.rejected_eins = rejected
    match.ein = None
    match.matched_name = None
    match.matched_city = None
    match.matched_state = None
    match.score = None
    match.status = MatchStatus.REJECTED
    match.decided_at = utcnow()
    match.decided_by = decided_by
    session.commit()

    return _after_decision(request, session, organization_id, "rejected")


def _find_candidate(match, ein: str) -> dict | None:
    for candidate in match.candidates or []:
        if candidate.get("ein") == ein:
            return candidate
    return None


def _after_decision(
    request: Request, session: Session, organization_id: int, outcome: str
):
    """Return a fragment for fetch callers, or redirect a plain form post."""
    if request.headers.get("X-Requested-With") == "fetch":
        return templates.TemplateResponse(
            request,
            "partials/decision.html",
            {
                "request": request,
                "outcome": outcome,
                "organization_id": organization_id,
                "remaining": summarize(session).needs_review,
            },
        )
    return RedirectResponse("/review", status_code=303)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


@app.get("/export.csv")
def export_csv(
    session: Session = Depends(get_db), filters: Filters = Depends(get_filters)
):
    rows, _ = fetch_rows(session, filters)
    body = exports.to_csv(rows, config, filters, data_status(session))
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{exports.export_filename("csv")}"'
            )
        },
    )


@app.get("/export.xlsx")
def export_xlsx(
    session: Session = Depends(get_db), filters: Filters = Depends(get_filters)
):
    rows, _ = fetch_rows(session, filters)
    body = exports.to_xlsx(rows, config, filters, data_status(session))
    return Response(
        content=body,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{exports.export_filename("xlsx")}"'
            )
        },
    )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


@app.post("/refresh")
def start_refresh(force: bool = Query(False)):
    started = manager.start(config, force_refresh=force)
    return JSONResponse(
        {"started": started, **manager.state.as_dict()},
        status_code=202 if started else 409,
    )


@app.get("/refresh/status")
def refresh_status():
    return JSONResponse(manager.state.as_dict())


@app.get("/healthz")
def healthz():
    return {"status": "ok", "app": config.app.name}


def _distinct_states(session: Session) -> list[str]:
    from sqlalchemy import select

    from app.models import Organization

    return [
        state
        for state in session.scalars(
            select(Organization.state)
            .where(Organization.state.is_not(None))
            .distinct()
            .order_by(Organization.state)
        ).all()
    ]
