"""ProPublica Nonprofit Explorer API client.

The API is free and unauthenticated, which makes politeness our responsibility:
requests are throttled to a configured rate, retried with exponential backoff on
429/5xx, and cached in the local database so a re-run only fetches what is new
or stale.

Every response also degrades: when the API is unreachable and a cached copy
exists -- even an expired one -- the cached payload is returned and flagged
stale, so callers can carry on and the UI can label the data.

Endpoints used (both documented at https://projects.propublica.org/nonprofits/api):

* ``GET /search.json?q=<name>&state%5Bid%5D=<ST>`` -- name search
* ``GET /organizations/<ein>.json`` -- organization detail plus filings
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    ApiCache,
    EinMatch,
    Filing,
    IngestRun,
    MatchStatus,
    Organization,
    RunStatus,
    utcnow,
)


class ProPublicaUnavailable(RuntimeError):
    """The API could not be reached and no cached payload was available."""


@dataclass
class ApiResult:
    """A payload plus how it was obtained, so freshness can be reported."""

    payload: dict[str, Any] | None
    status_code: int
    fetched_at: datetime
    from_cache: bool = False
    stale: bool = False

    @property
    def found(self) -> bool:
        return self.status_code == 200 and self.payload is not None


class RateLimiter:
    """Simple spacing limiter: never issue requests closer than 1/rate apart."""

    def __init__(
        self,
        per_second: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call: float | None = None

    def wait(self) -> None:
        if self.interval <= 0:
            return
        now = self._monotonic()
        if self._last_call is not None:
            remaining = self.interval - (now - self._last_call)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_call = now


@dataclass
class SearchHit:
    """One organization returned by the search endpoint."""

    ein: str
    name: str
    city: str | None
    state: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> "SearchHit | None":
        ein = item.get("ein")
        name = item.get("name")
        if ein is None or not name:
            return None
        return cls(
            ein=normalize_ein(str(ein)),
            name=str(name),
            city=(item.get("city") or None),
            state=(item.get("state") or None),
            raw=item,
        )


def normalize_ein(ein: str | int | None) -> str:
    """Return a 9-digit EIN string, zero-padded (the API drops leading zeros)."""
    if ein is None:
        return ""
    digits = "".join(ch for ch in str(ein) if ch.isdigit())
    return digits.zfill(9) if digits else ""


def format_ein(ein: str | None) -> str | None:
    """Display form ``12-3456789``."""
    if not ein:
        return None
    digits = normalize_ein(ein)
    return f"{digits[:2]}-{digits[2:]}" if len(digits) == 9 else ein


class ProPublicaClient:
    """Throttled, retrying, database-cached client for Nonprofit Explorer."""

    def __init__(
        self,
        config: Config,
        session: Session,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.settings = config.propublica
        self.session = session
        self._sleep = sleep
        self._owns_client = client is None
        self._http = client or httpx.Client(
            timeout=self.settings.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "FQHC-Prospect-Intelligence/1.0 (+allstar.partners)",
                "Accept": "application/json",
            },
        )
        self._limiter = RateLimiter(
            self.settings.requests_per_second, sleep=sleep, monotonic=monotonic
        )

        # Counters for progress reporting.
        self.live_requests = 0
        self.cache_hits = 0
        self.stale_responses = 0

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "ProPublicaClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- cache ---------------------------------------------------------------

    def _cached(self, cache_key: str) -> ApiCache | None:
        return self.session.scalars(
            select(ApiCache).where(ApiCache.cache_key == cache_key)
        ).first()

    def _store(
        self, cache_key: str, url: str, status_code: int, payload: dict[str, Any] | None
    ) -> datetime:
        entry = self._cached(cache_key)
        stamp = utcnow()
        if entry is None:
            entry = ApiCache(cache_key=cache_key, url=url)
            self.session.add(entry)
        entry.url = url
        entry.status_code = status_code
        entry.payload = payload
        entry.fetched_at = stamp
        self.session.commit()
        return stamp

    def _is_fresh(self, entry: ApiCache) -> bool:
        age = utcnow() - _as_utc(entry.fetched_at)
        return age < timedelta(days=self.settings.refresh_after_days)

    # -- HTTP ----------------------------------------------------------------

    def _request(self, url: str, params: dict[str, Any]) -> tuple[int, dict | None]:
        """Issue one throttled request, retrying transient failures.

        Returns ``(status_code, payload)``. A 404 is a legitimate negative
        answer (the EIN is not in the database) and returns ``(404, None)``
        rather than raising.
        """
        attempts = self.settings.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            self._limiter.wait()
            try:
                response = self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    self._sleep(self._backoff(attempt))
                    continue
                break

            self.live_requests += 1
            status = response.status_code

            if status == 404:
                return status, None

            if status == 429 or status >= 500:
                last_error = httpx.HTTPStatusError(
                    f"HTTP {status}", request=response.request, response=response
                )
                if attempt + 1 < attempts:
                    self._sleep(self._retry_delay(response, attempt))
                    continue
                break

            if status >= 400:
                # A 4xx other than 404/429 will not fix itself; do not retry.
                raise ProPublicaUnavailable(f"{url} returned HTTP {status}")

            try:
                return status, response.json()
            except ValueError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    self._sleep(self._backoff(attempt))
                    continue
                break

        raise ProPublicaUnavailable(f"{url} failed after {attempts} attempts: {last_error}")

    def _backoff(self, attempt: int) -> float:
        return min(
            self.settings.backoff_base_seconds * (2**attempt),
            self.settings.backoff_max_seconds,
        )

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """Honour Retry-After when the server sends it; otherwise back off."""
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), self.settings.backoff_max_seconds)
            except ValueError:
                pass
        return self._backoff(attempt)

    # -- public API ----------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        cache_key: str,
        force: bool = False,
    ) -> ApiResult:
        """Fetch a JSON endpoint, preferring a fresh cached copy."""
        params = params or {}
        url = f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"

        entry = self._cached(cache_key)
        if entry is not None and not force and self._is_fresh(entry):
            self.cache_hits += 1
            return ApiResult(
                payload=entry.payload,
                status_code=entry.status_code,
                fetched_at=_as_utc(entry.fetched_at),
                from_cache=True,
            )

        try:
            status, payload = self._request(url, params)
        except ProPublicaUnavailable:
            if entry is not None:
                # Expired, but better than nothing -- flagged so the caller can
                # report that it is running on stale data.
                self.stale_responses += 1
                return ApiResult(
                    payload=entry.payload,
                    status_code=entry.status_code,
                    fetched_at=_as_utc(entry.fetched_at),
                    from_cache=True,
                    stale=True,
                )
            raise

        fetched_at = self._store(cache_key, url, status, payload)
        return ApiResult(payload=payload, status_code=status, fetched_at=fetched_at)

    def search(
        self, query: str, state: str | None = None, *, force: bool = False
    ) -> tuple[list[SearchHit], ApiResult]:
        """Search organizations by name, optionally constrained to a state."""
        params: dict[str, Any] = {"q": query}
        if state:
            # The API expects the bracketed form state[id]=IL.
            params["state[id]"] = state

        cache_key = f"search:{state or '*'}:{query.strip().lower()}"
        result = self.get("search.json", params, cache_key=cache_key, force=force)

        payload = result.payload or {}
        hits = [
            hit
            for hit in (
                SearchHit.from_payload(item)
                for item in payload.get("organizations", []) or []
            )
            if hit is not None
        ]
        return hits, result

    def organization(self, ein: str, *, force: bool = False) -> ApiResult:
        """Fetch one organization by EIN, including its filings."""
        digits = normalize_ein(ein)
        return self.get(
            f"organizations/{int(digits)}.json",
            cache_key=f"org:{digits}",
            force=force,
        )


def _as_utc(value: datetime) -> datetime:
    """SQLite round-trips datetimes without tzinfo; restore it."""
    from datetime import timezone

    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ===========================================================================
# Form 990 enrichment
# ===========================================================================

# ProPublica exposes different field names depending on which form the
# organization filed (990 vs 990-EZ vs 990-PF), so each figure is read through
# a list of candidate keys. An unknown key set yields None -- never a zero.
# NOTE: totprgmrevnue is deliberately absent here. It is *program service*
# revenue -- billing income only -- not total revenue, and using it as a
# fallback would silently understate an organization by the size of its grants.
REVENUE_KEYS = ("totrevenue", "totrevnue", "revenue_amount")
EXPENSE_KEYS = ("totfuncexpns", "totexpns", "totexpenses", "expenses_amount")
ASSET_KEYS = ("totassetsend", "totassetsendofyear", "totassets", "assets_amount")
LIABILITY_KEYS = ("totliabend", "totliabendofyear", "totliabilities", "liabilities_amount")

# Employees on a W-2 in the calendar year (Form 990 Part I line 5). Not a
# financial figure, but the one headcount the IRS publishes.
EMPLOYEE_KEYS = ("noemplyeesw3cnt", "numberofemployees", "totemployee")

# Revenue composition: the funding mix behind the total.
CONTRIBUTION_KEYS = ("totcntrbgfts", "totcntrbs", "contributions_amount")
PROGRAM_REVENUE_KEYS = ("totprgmrevnue", "prgmservrev", "program_service_revenue")
INVESTMENT_KEYS = ("invstmntinc", "investinc", "investment_income")
GOVERNMENT_GRANT_KEYS = ("govtgrants", "grntsfrmgovt", "government_grants")

FORM_TYPE_NAMES = {0: "990", 1: "990-EZ", 2: "990-PF", 3: "990-N"}


def _first_number(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Return the first present, numeric value among ``keys``."""
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    """As ``_first_number``, for a count rather than an amount."""
    value = _first_number(payload, keys)
    return int(value) if value is not None else None


def _parse_period_end(tax_prd: Any) -> datetime | None:
    """Convert ProPublica's ``tax_prd`` (YYYYMM) into the period end date."""
    import calendar
    from datetime import timezone

    text = str(tax_prd or "").strip()
    if len(text) < 6 or not text[:6].isdigit():
        return None
    year, month = int(text[:4]), int(text[4:6])
    if not 1 <= month <= 12 or not 1900 <= year <= 2200:
        return None
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, last_day, tzinfo=timezone.utc)


def _parse_updated(value: Any) -> datetime | None:
    """Parse ProPublica's ``updated`` timestamp, which is ISO-8601 with offset."""
    from datetime import timezone

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(
        tzinfo=timezone.utc
    )


@dataclass
class FilingRecord:
    """One 990 filing, normalized. Missing figures stay None."""

    ein: str
    tax_year: int
    total_revenue: float | None = None
    total_expenses: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    employee_count: int | None = None
    contributions: float | None = None
    program_service_revenue: float | None = None
    investment_income: float | None = None
    government_grants: float | None = None
    form_type: str | None = None
    pdf_url: str | None = None
    period_end: datetime | None = None
    filing_date: datetime | None = None

    @property
    def has_financials(self) -> bool:
        return any(
            v is not None
            for v in (self.total_revenue, self.total_expenses, self.total_assets)
        )

    @property
    def has_composition(self) -> bool:
        """Whether any funding-mix detail is available for this filing."""
        return any(
            v is not None
            for v in (
                self.contributions,
                self.program_service_revenue,
                self.investment_income,
                self.government_grants,
            )
        )


def parse_filings(
    ein: str, payload: dict[str, Any] | None, *, limit: int = 3
) -> list[FilingRecord]:
    """Extract the most recent filings from an organization payload.

    Filings ProPublica has extracted data from win over bare PDF listings for
    the same tax year, but a year with only a PDF is still returned -- with null
    figures -- so the UI can offer the document and say the numbers are not
    available.
    """
    if not payload:
        return []

    digits = normalize_ein(ein)
    by_year: dict[int, FilingRecord] = {}

    def absorb(items: Any, with_data: bool) -> None:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            year = item.get("tax_prd_yr")
            try:
                tax_year = int(year)
            except (TypeError, ValueError):
                period = _parse_period_end(item.get("tax_prd"))
                if period is None:
                    continue
                tax_year = period.year

            record = FilingRecord(
                ein=digits,
                tax_year=tax_year,
                total_revenue=_first_number(item, REVENUE_KEYS) if with_data else None,
                total_expenses=_first_number(item, EXPENSE_KEYS) if with_data else None,
                total_assets=_first_number(item, ASSET_KEYS) if with_data else None,
                total_liabilities=(
                    _first_number(item, LIABILITY_KEYS) if with_data else None
                ),
                employee_count=_first_int(item, EMPLOYEE_KEYS) if with_data else None,
                contributions=(
                    _first_number(item, CONTRIBUTION_KEYS) if with_data else None
                ),
                program_service_revenue=(
                    _first_number(item, PROGRAM_REVENUE_KEYS) if with_data else None
                ),
                investment_income=(
                    _first_number(item, INVESTMENT_KEYS) if with_data else None
                ),
                government_grants=(
                    _first_number(item, GOVERNMENT_GRANT_KEYS) if with_data else None
                ),
                form_type=FORM_TYPE_NAMES.get(item.get("formtype")),
                pdf_url=item.get("pdf_url") or None,
                period_end=_parse_period_end(item.get("tax_prd")),
                filing_date=_parse_updated(item.get("updated")),
            )

            existing = by_year.get(tax_year)
            if existing is None or (record.has_financials and not existing.has_financials):
                by_year[tax_year] = record
            elif existing.pdf_url is None and record.pdf_url:
                # Keep the extracted figures but adopt the document link.
                existing.pdf_url = record.pdf_url

    absorb(payload.get("filings_with_data"), with_data=True)
    absorb(payload.get("filings_without_data"), with_data=False)

    ordered = sorted(by_year.values(), key=lambda f: f.tax_year, reverse=True)
    return ordered[:limit]


def latest_with_financials(filings: list[FilingRecord]) -> FilingRecord | None:
    """The most recent filing that actually carries figures, if any."""
    for filing in sorted(filings, key=lambda f: f.tax_year, reverse=True):
        if filing.has_financials:
            return filing
    return None


@dataclass
class EnrichmentResult:
    eligible: int = 0
    fetched: int = 0
    from_cache: int = 0
    not_found: int = 0
    filings_written: int = 0
    organizations_with_financials: int = 0
    failed: int = 0
    used_cache: bool = False
    source_reachable: bool = True
    messages: list[str] = field(default_factory=list)

    @property
    def status(self) -> RunStatus:
        if self.fetched == 0 and self.failed > 0:
            return RunStatus.FAILED
        return RunStatus.PARTIAL if not self.source_reachable else RunStatus.SUCCESS


# Give up after this many consecutive hard failures rather than hammering an
# API that is clearly unreachable.
CONSECUTIVE_FAILURE_LIMIT = 3


def enrich_financials(
    session: Session,
    config: Config,
    *,
    client: ProPublicaClient,
    force: bool = False,
    limit: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> EnrichmentResult:
    """Fetch and store 990 filings for every organization with a usable EIN.

    Only organizations whose EIN match is auto-accepted or human-approved are
    enriched: financials must never be attached to an organization on the
    strength of an unconfirmed guess.
    """
    report = on_progress or (lambda _message: None)
    result = EnrichmentResult()

    run = IngestRun(stage="financials", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    statement = (
        select(Organization)
        .join(EinMatch, EinMatch.organization_id == Organization.id)
        .where(
            EinMatch.ein.is_not(None),
            EinMatch.status.in_([MatchStatus.AUTO.value, MatchStatus.ACCEPTED.value]),
        )
        .order_by(Organization.name)
    )
    footprint = config.api_states
    if footprint:
        statement = statement.where(Organization.state.in_(footprint))

    organizations = session.scalars(statement).all()
    result.eligible = len(organizations)
    report(f"{result.eligible:,} organizations have a confirmed EIN")

    consecutive_failures = 0

    try:
        for index, org in enumerate(organizations, start=1):
            # Count cache hits as well as live fetches, so --limit means the same
            # "at most N organizations" here as it does in the matching stage.
            if limit is not None and result.fetched + result.from_cache >= limit:
                break

            ein = org.ein
            if not ein:  # defensive: the query already filters for this
                continue

            try:
                api_result = client.organization(ein, force=force)
            except ProPublicaUnavailable as exc:
                result.failed += 1
                result.source_reachable = False
                consecutive_failures += 1
                report(f"ProPublica unreachable for {org.name} (EIN {ein}): {exc}")
                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    result.messages.append(
                        f"ProPublica unreachable ({exc}); stopped after "
                        f"{consecutive_failures} consecutive failures with "
                        f"{result.fetched} of {result.eligible} organizations enriched"
                    )
                    break
                continue

            consecutive_failures = 0
            if api_result.from_cache:
                result.used_cache = True
                result.from_cache += 1
            else:
                result.fetched += 1

            if api_result.status_code == 404:
                result.not_found += 1
                continue

            # The organization block carries the NTEE classification, which is
            # the closest thing to a program area the IRS publishes.
            details = (api_result.payload or {}).get("organization") or {}
            ntee = details.get("ntee_code")
            if ntee:
                org.ntee_code = str(ntee).strip().upper()

            filings = parse_filings(
                ein, api_result.payload, limit=config.propublica.filings_per_org
            )
            if not filings:
                continue

            written = _persist_filings(session, ein, filings)
            result.filings_written += written
            if any(f.has_financials for f in filings):
                result.organizations_with_financials += 1

            if index % 50 == 0:
                report(
                    f"Enriched {index:,} of {result.eligible:,} organizations "
                    f"({result.filings_written:,} filings stored)"
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
            f"{client.stale_responses} response(s) served from an expired cache "
            "because ProPublica was unreachable"
        )

    report(
        f"Stored {result.filings_written:,} filings for "
        f"{result.organizations_with_financials:,} organizations"
    )

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.eligible
    run.records_written = result.filings_written
    run.used_cache = result.used_cache
    run.source_reachable = result.source_reachable
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result


def _persist_filings(session: Session, ein: str, filings: list[FilingRecord]) -> int:
    """Upsert filings for one EIN and drop any that are no longer retained."""
    existing = {
        filing.tax_year: filing
        for filing in session.scalars(select(Filing).where(Filing.ein == ein)).all()
    }
    keep_years = {record.tax_year for record in filings}

    for record in filings:
        row = existing.get(record.tax_year)
        if row is None:
            row = Filing(ein=ein, tax_year=record.tax_year)
            session.add(row)
        row.total_revenue = record.total_revenue
        row.total_expenses = record.total_expenses
        row.total_assets = record.total_assets
        row.total_liabilities = record.total_liabilities
        row.employee_count = record.employee_count
        row.contributions = record.contributions
        row.program_service_revenue = record.program_service_revenue
        row.investment_income = record.investment_income
        row.government_grants = record.government_grants
        row.form_type = record.form_type
        row.pdf_url = record.pdf_url
        row.period_end = record.period_end
        row.filing_date = record.filing_date
        row.fetched_at = utcnow()

    # Retain only the configured window, so shrinking filings_per_org does not
    # leave orphaned years behind.
    for year, row in existing.items():
        if year not in keep_years:
            session.delete(row)

    session.flush()
    return len(filings)
