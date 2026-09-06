"""The outreach research brief: six sections, every claim sourced."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    Contractor,
    EinMatch,
    GranteeType,
    MatchStatus,
    Organization,
    Person,
    Site,
    UdsReport,
    WebsiteCrawl,
    WebsitePerson,
)
from pipeline.dossier import build, render, render_all

NOW = datetime.now(timezone.utc)


def health_center(session, name="Near North Health", **kwargs):
    defaults = dict(
        dedup_key=name.lower(),
        name=name,
        normalized_name=name.lower(),
        city="Chicago",
        state="IL",
        grantee_type=GranteeType.AWARDEE,
        last_seen_at=NOW,
    )
    defaults.update(kwargs)
    organization = Organization(**defaults)
    session.add(organization)
    session.flush()
    return organization


def test_every_section_is_present(session) -> None:
    organization = health_center(session, website="https://nn.org", phone="(312) 337-1073")
    session.commit()

    text = render(build(session, organization))

    for heading in (
        "1. Official name and website",
        "2. Main location and service area",
        "3. Operational priorities",
        "4. Published general contact",
        "5. Decision-maker roles",
        "6. Sources and verification",
    ):
        assert heading in text


def test_a_role_is_always_given_even_when_no_name_is_known(session) -> None:
    """An unfilled role is a research task; a fabricated name is a liability."""
    organization = health_center(session)
    session.commit()

    dossier = build(session, organization)
    text = render(dossier)

    assert dossier.named_people == 0
    assert len(dossier.people) == 3
    assert "name not established" in text
    assert "the roles above are the research task, not a finding" in text


def test_a_named_role_beats_an_unnamed_one(session) -> None:
    """The first version dropped a named chief executive for a blank finance row."""
    organization = health_center(session, website="https://nn.org")
    session.add(
        EinMatch(organization_id=organization.id, ein="363197647", score=97.0,
                 status=MatchStatus.AUTO)
    )
    session.add(
        UdsReport(organization_id=organization.id, year=2024,
                  director_name="TRISTE LIETEAU SMITH",
                  director_email="tsmith@nn.org")
    )
    session.add(
        Person(ein="363197647", tax_year=2023, name="ANTHONY V LEE",
               title="CHIEF INFORMATION OFFICER", roles=["Key employee"])
    )
    session.add(
        WebsitePerson(organization_id=organization.id, name="Kwame Osei",
                      title="Director of Compliance",
                      source_url="https://nn.org/leadership", fetched_at=NOW)
    )
    session.commit()

    dossier = build(session, organization)

    assert dossier.named_people == 3
    labels = [label for label, _why, _person in dossier.people]
    assert "Chief executive" in labels


def test_a_name_from_a_web_page_is_marked_for_verification(session) -> None:
    organization = health_center(session, website="https://nn.org")
    session.add(
        WebsitePerson(organization_id=organization.id, name="Kwame Osei",
                      title="Chief Information Officer",
                      source_url="https://nn.org/leadership", fetched_at=NOW)
    )
    session.commit()

    text = render(build(session, organization))

    assert "Kwame Osei ⚠" in text
    assert "confirm against the linked page" in text
    assert "https://nn.org/leadership" in text


def test_a_name_from_a_filing_says_which_tax_year(session) -> None:
    organization = health_center(session)
    session.add(
        EinMatch(organization_id=organization.id, ein="363197647", score=97.0,
                 status=MatchStatus.AUTO)
    )
    session.add(
        Person(ein="363197647", tax_year=2023, name="ANTHONY V LEE",
               title="CHIEF INFORMATION OFFICER", roles=["Key employee"])
    )
    session.commit()

    text = render(build(session, organization))

    assert "IRS Form 990 Part VII, FY2023" in text
    assert "confirm the post is still held" in text


def test_no_email_is_ever_guessed(session) -> None:
    organization = health_center(session, website="https://nn.org")
    session.commit()

    text = render(build(session, organization))

    assert "Do not guess an address from the domain" in text
    assert "never against a guessed address" in text
    assert "@nn.org" not in text


def test_the_uds_director_supplies_the_published_contact(session) -> None:
    organization = health_center(session)
    session.add(
        UdsReport(organization_id=organization.id, year=2024,
                  director_name="Grace Okoro", director_email="gokoro@nn.org")
    )
    session.commit()

    text = render(build(session, organization))

    assert "gokoro@nn.org" in text
    assert "HRSA UDS 2024, project director" in text


def test_operational_priorities_come_from_held_sources(session) -> None:
    organization = health_center(
        session,
        site_count=8,
        federal_award_amount=9_842_113,
        funding_programs=["Community Health Center"],
    )
    session.add(
        EinMatch(organization_id=organization.id, ein="363197647", score=97.0,
                 status=MatchStatus.AUTO)
    )
    session.add(
        UdsReport(organization_id=organization.id, year=2024, patients=84532,
                  total_fte=612.4, ehr_vendor="Epic Systems Corporation")
    )
    session.add(
        Contractor(ein="363197647", tax_year=2023, name="Epic Systems Corporation",
                   services="EHR hosting", compensation=1_240_000)
    )
    session.commit()

    text = render(build(session, organization))

    assert "84,532 patients served (2024 UDS)" in text
    assert "612 staff FTE" in text
    assert "Epic Systems Corporation" in text
    assert "$9.8M Section 330 federal award" in text


def test_the_service_area_comes_from_the_delivery_sites(session) -> None:
    organization = health_center(session)
    for city in ("Chicago", "Chicago", "Evanston", "Cicero"):
        session.add(Site(organization_id=organization.id, name=f"{city} site",
                         city=city, state="IL"))
    session.commit()

    text = render(build(session, organization))

    assert "Chicago, Cicero, Evanston" in text


def test_an_unconfirmed_ein_is_flagged(session) -> None:
    organization = health_center(session)
    session.add(
        EinMatch(organization_id=organization.id, ein=None, score=78.0,
                 status=MatchStatus.PENDING)
    )
    session.commit()

    text = render(build(session, organization))

    assert "Requires manual verification" in text
    assert "review queue" in text


def test_a_missing_website_is_flagged_rather_than_left_blank(session) -> None:
    organization = health_center(session)
    session.commit()

    text = render(build(session, organization))

    assert "not published by HRSA — manual verification required" in text
    assert "find the official site manually" in text


def test_a_stale_crawl_is_flagged(session) -> None:
    organization = health_center(session, website="https://nn.org")
    session.add(
        WebsiteCrawl(organization_id=organization.id, url="https://nn.org",
                     outcome="leadership page found",
                     fetched_at=NOW - timedelta(days=500))
    )
    session.commit()

    text = render(build(session, organization))

    assert "re-check before use" in text


def test_the_document_states_its_boundaries(session) -> None:
    organization = health_center(session)
    session.commit()

    text = render_all([build(session, organization)], state="IL")

    assert "No patient data of any kind is held or used" in text
    assert "Pages behind a login, patient portals" in text
    assert "Nothing was inferred" in text
