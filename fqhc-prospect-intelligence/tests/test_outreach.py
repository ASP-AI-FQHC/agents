"""Outreach guardrails: suppression, the compliance block, and what the
crawler refuses to read."""

from __future__ import annotations

import pytest

from app.outreach import (
    OPT_OUT_BUSINESS_DAYS,
    Suppression,
    compliance_block,
    load_suppression,
)
from pipeline.website import is_patient_facing


# ---------------------------------------------------------------------------
# The crawler's own boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/patient-portal",
        "https://example.org/patient-portals",
        "https://example.org/mychart",
        "https://example.org/login",
        "https://example.org/sign-in",
        "https://example.org/my-account",
        "https://example.org/pay-bill",
        "https://example.org/appointments",
        "https://example.org/medical-records",
        "https://example.org/prescription-refill",
        "https://example.org/new-patient-forms",
        "https://example.org/index?page=login",
        "https://example.org/telehealth-visit",
    ],
)
def test_patient_facing_pages_are_refused(url) -> None:
    """robots.txt is a request; this is a rule.

    A portal, a bill payment page and an appointment form are where a person's
    own health and payment details live. None has ever held a leadership
    listing, so there is nothing to gain by reading them.
    """
    assert is_patient_facing(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/leadership",
        "https://example.org/about/board-of-directors",
        "https://example.org/our-team",
        "https://example.org/reporting",          # contains "port"
        "https://example.org/transportation",     # contains "port"
        "https://example.org/support-us",
        "https://portalhealth.org/leadership",    # the host, not the path
        "https://example.org/accountability",
    ],
)
def test_ordinary_pages_are_still_read(url) -> None:
    assert not is_patient_facing(url)


def test_the_refusal_happens_at_the_fetcher(config, monkeypatch) -> None:
    """So a URL from any source passes the same gate -- navigation, search or
    a redirect."""
    import httpx

    from pipeline.propublica import RateLimiter
    from pipeline.website import SiteFetcher

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="<html></html>",
                              headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = SiteFetcher(config, client=client, limiter=RateLimiter(1.0, sleep=lambda _s: None))
    with fetcher:
        assert fetcher.fetch("https://example.org/patient-portal") is None

    assert not any("patient-portal" in url for url in requested)


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


def test_a_missing_file_is_empty_not_an_error(tmp_path) -> None:
    suppression = load_suppression(tmp_path / "nope.txt")
    assert suppression.is_empty
    assert suppression.blocks(email="anyone@example.org") is None


def test_the_three_shapes_an_opt_out_arrives_in(tmp_path) -> None:
    path = tmp_path / "suppression.txt"
    path.write_text(
        "# asked to be removed by phone, 4 September\n"
        "grace.okoro@example.org\n"
        "\n"
        "@blockedcenter.org      # nobody at this health center\n"
        "another-domain.org\n"
        "Erie Family Health Centers\n",
        encoding="utf-8",
    )

    suppression = load_suppression(path)

    assert suppression.blocks(email="Grace.Okoro@example.org")
    assert suppression.blocks(email="anyone@blockedcenter.org")
    assert suppression.blocks(email="anyone@another-domain.org")
    assert suppression.blocks(organization="ERIE FAMILY HEALTH CENTERS")
    assert suppression.blocks(email="someone@allowed.org") is None


def test_the_reason_says_which_entry_did_it(tmp_path) -> None:
    """An audit needs to know whether a person opted out or their whole
    organization did."""
    path = tmp_path / "s.txt"
    path.write_text("person@x.org\n@y.org\n", encoding="utf-8")
    suppression = load_suppression(path)

    assert "address" in suppression.blocks(email="person@x.org")
    assert "domain y.org" in suppression.blocks(email="anyone@y.org")


def test_comments_and_blank_lines_are_ignored(tmp_path) -> None:
    path = tmp_path / "s.txt"
    path.write_text("# a comment alone\n\n   \nreal@example.org\n", encoding="utf-8")
    suppression = load_suppression(path)
    assert suppression.total == 1


def test_suppressed_contacts_are_removed_from_an_export_not_marked() -> None:
    """A row still in the file is a row somebody can still email."""
    from types import SimpleNamespace

    from app.exports import apply_suppression

    def contact(name, email, org):
        return SimpleNamespace(
            name=name, email=email, organization=SimpleNamespace(name=org)
        )

    contacts = [
        contact("Grace Okoro", "grace@opted-out.org", "Alpha Health"),
        contact("Lee Adams", "lee@fine.org", "Beta Health"),
        contact("Sam Reed", None, "Gamma Health"),
    ]
    suppression = Suppression(domains={"opted-out.org"}, organizations={"gammahealth"})

    kept, removed = apply_suppression(contacts, suppression)

    assert removed == 2
    assert [c.name for c in kept] == ["Lee Adams"]


def test_no_suppression_list_changes_nothing() -> None:
    from app.exports import apply_suppression

    rows = [object(), object()]
    kept, removed = apply_suppression(rows, None)
    assert kept == rows and removed == 0


# ---------------------------------------------------------------------------
# The compliance block
# ---------------------------------------------------------------------------


def test_the_block_states_the_obligations_that_apply_to_b2b() -> None:
    text = " ".join(
        compliance_block(
            "Allstar Partners",
            "954 W. Washington Blvd. Ste 535, Chicago, IL 60607",
            "Reply with UNSUBSCRIBE",
        )
    )

    assert "business-to-business" in text
    assert f"{OPT_OUT_BUSINESS_DAYS} business" in text
    assert "physical postal address" in text
    assert "954 W. Washington Blvd" in text
    assert "Reply with UNSUBSCRIBE" in text
    assert "No patient information" in text
    assert "Not legal advice" in text


def test_a_missing_postal_address_is_called_out_not_omitted() -> None:
    """Silently leaving it out is how a message ships without one."""
    text = " ".join(compliance_block("Allstar Partners", None, None))

    assert "NOT SET" in text
    assert "does not comply" in text
