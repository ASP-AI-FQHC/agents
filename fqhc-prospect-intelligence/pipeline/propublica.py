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
from app.models import ApiCache, utcnow


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
