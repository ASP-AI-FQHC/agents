"""ProPublica client: throttling, retries, caching and degradation."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import ApiCache, utcnow
from pipeline.propublica import (
    ProPublicaClient,
    ProPublicaUnavailable,
    RateLimiter,
    format_ein,
    normalize_ein,
)


class FakeClock:
    """Deterministic clock so rate limiting and backoff are testable."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_client(
    config: Config,
    session: Session,
    handler,
    clock: FakeClock | None = None,
) -> ProPublicaClient:
    clock = clock or FakeClock()
    return ProPublicaClient(
        config,
        session,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


SEARCH_PAYLOAD = {
    "total_results": 1,
    "organizations": [
        {
            "ein": 362167869,
            "name": "ERIE FAMILY HEALTH CENTER INC",
            "city": "CHICAGO",
            "state": "IL",
            "have_filings": True,
        }
    ],
}


# ---------------------------------------------------------------------------
# EIN handling
# ---------------------------------------------------------------------------


def test_ein_leading_zeros_are_preserved() -> None:
    """The API returns EINs as integers, which drops the leading zero."""
    assert normalize_ein(42103594) == "042103594"
    assert normalize_ein("04-2103594") == "042103594"
    assert format_ein("042103594") == "04-2103594"
    assert normalize_ein(None) == ""
    assert format_ein(None) is None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_requests_are_spaced_to_the_configured_rate() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1.0, sleep=clock.sleep, monotonic=clock.monotonic)

    limiter.wait()          # first call is immediate
    limiter.wait()          # second must wait a full second
    assert clock.slept == [1.0]

    clock.now += 5.0        # a long gap needs no wait at all
    limiter.wait()
    assert clock.slept == [1.0]


def test_client_throttles_successive_requests(config: Config, session: Session) -> None:
    clock = FakeClock()
    client = make_client(
        config, session, lambda _r: httpx.Response(200, json=SEARCH_PAYLOAD), clock
    )

    client.search("erie family health", "IL", force=True)
    client.search("milwaukee health services", "WI", force=True)

    assert clock.slept == [1.0]


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


def test_429_is_retried_with_exponential_backoff(
    config: Config, session: Session
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:
            return httpx.Response(429)
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    clock = FakeClock()
    client = make_client(config, session, handler, clock)
    hits, result = client.search("erie", "IL")

    assert calls["n"] == 4
    assert len(hits) == 1
    # 2s, 4s, 8s of backoff (plus the throttle spacing between attempts).
    assert [s for s in clock.slept if s in (2.0, 4.0, 8.0)] == [2.0, 4.0, 8.0]
    assert result.from_cache is False


def test_retry_after_header_is_honoured(config: Config, session: Session) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    clock = FakeClock()
    client = make_client(config, session, handler, clock)
    client.search("erie", "IL")

    assert 7.0 in clock.slept


def test_server_errors_are_retried(config: Config, session: Session) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] == 1 else 200, json=SEARCH_PAYLOAD)

    client = make_client(config, session, handler)
    hits, _ = client.search("erie", "IL")
    assert calls["n"] == 2
    assert len(hits) == 1


def test_client_errors_are_not_retried(config: Config, session: Session) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400)

    client = make_client(config, session, handler)
    with pytest.raises(ProPublicaUnavailable, match="HTTP 400"):
        client.search("erie", "IL")
    assert calls["n"] == 1


def test_persistent_failure_raises_after_max_retries(
    config: Config, session: Session
) -> None:
    config.propublica.max_retries = 2
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("blocked", request=request)

    client = make_client(config, session, handler)
    with pytest.raises(ProPublicaUnavailable, match="after 3 attempts"):
        client.search("erie", "IL")
    assert calls["n"] == 3


def test_404_is_a_negative_answer_not_an_error(
    config: Config, session: Session
) -> None:
    client = make_client(config, session, lambda _r: httpx.Response(404))
    result = client.organization("362167869")

    assert result.status_code == 404
    assert result.payload is None
    assert result.found is False


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_second_call_is_served_from_cache(config: Config, session: Session) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    client = make_client(config, session, handler)
    client.search("erie", "IL")
    _, result = client.search("erie", "IL")

    assert calls["n"] == 1
    assert result.from_cache is True
    assert client.cache_hits == 1


def test_force_bypasses_a_fresh_cache(config: Config, session: Session) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    client = make_client(config, session, handler)
    client.search("erie", "IL")
    client.search("erie", "IL", force=True)
    assert calls["n"] == 2


def test_expired_cache_entries_are_refetched(config: Config, session: Session) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    client = make_client(config, session, handler)
    client.search("erie", "IL")

    entry = session.scalars(select(ApiCache)).one()
    entry.fetched_at = utcnow() - timedelta(
        days=config.propublica.refresh_after_days + 1
    )
    session.commit()

    client.search("erie", "IL")
    assert calls["n"] == 2


def test_unreachable_api_falls_back_to_expired_cache(
    config: Config, session: Session
) -> None:
    """Stale data beats no data -- but it must be flagged as stale."""
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("blocked", request=request)
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    client = make_client(config, session, handler)
    client.search("erie", "IL")

    entry = session.scalars(select(ApiCache)).one()
    entry.fetched_at = utcnow() - timedelta(days=365)
    session.commit()
    state["fail"] = True

    hits, result = client.search("erie", "IL")

    assert len(hits) == 1
    assert result.from_cache is True
    assert result.stale is True
    assert client.stale_responses == 1


def test_unreachable_api_without_cache_raises(config: Config, session: Session) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked", request=request)

    client = make_client(config, session, handler)
    with pytest.raises(ProPublicaUnavailable):
        client.search("erie", "IL")


def test_search_sends_the_state_filter(config: Config, session: Session) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    make_client(config, session, handler).search("erie family health", "IL")

    assert "q=erie" in seen["url"]
    assert "state%5Bid%5D=IL" in seen["url"]


def test_organization_endpoint_uses_the_numeric_ein(
    config: Config, session: Session
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"organization": {"ein": 362167869}})

    make_client(config, session, handler).organization("36-2167869")
    assert seen["url"].endswith("/organizations/362167869.json")
