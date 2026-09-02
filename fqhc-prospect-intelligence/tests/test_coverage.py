"""The coverage report: what is missing, and the next action for each gap."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    EinMatch,
    GranteeType,
    MatchStatus,
    Organization,
    Person,
    UdsReport,
    WebsiteCrawl,
    WebsitePerson,
)
from pipeline.coverage import build, render


def organization(session, name, *, state="IL", website=None, ein=None, status=None):
    org = Organization(
        dedup_key=f"{name.lower()}|{state}",
        name=name,
        normalized_name=name.lower(),
        state=state,
        website=website,
        grantee_type=GranteeType.AWARDEE,
    )
    session.add(org)
    session.flush()
    if ein or status is not None:
        session.add(
            EinMatch(
                organization_id=org.id,
                ein=ein,
                score=95.0 if ein else None,
                status=status or MatchStatus.AUTO,
            )
        )
    session.commit()
    return org


def test_the_uds_director_counts_as_the_chief_executive(session) -> None:
    """HRSA asks for the person who runs the health center."""
    org = organization(session, "Alpha Health", ein="111111111")
    session.add(
        UdsReport(
            organization_id=org.id, year=2024,
            director_name="Amara Nwosu", director_email="anwosu@alpha.org",
        )
    )
    session.commit()

    report = build(session, "IL")
    entry = report.organizations[0]

    assert entry.chief_executive == "Amara Nwosu"
    assert entry.chief_executive_email == "anwosu@alpha.org"
    assert entry.chief_executive_source == "HRSA UDS 2024"
    assert report.with_email == [entry]


def test_a_filing_supplies_the_name_but_never_an_email(session) -> None:
    org = organization(session, "Beta Health", ein="222222222")
    session.add(
        Person(
            ein="222222222", tax_year=2023, name="DANIEL RUIZ",
            title="CHIEF EXECUTIVE OFFICER", roles=["Officer"],
        )
    )
    session.commit()

    entry = build(session, "IL").organizations[0]
    assert entry.chief_executive == "DANIEL RUIZ"
    assert entry.chief_executive_source == "Form 990 FY2023"
    assert entry.chief_executive_email is None
    assert "no UDS return loaded" in " ".join(entry.reasons)


def test_a_board_only_filing_is_not_a_chief_executive(session) -> None:
    """The gap has to be reported, not filled with whoever was to hand."""
    org = organization(session, "Gamma Health", ein="333333333")
    for name, title in [
        ("JAMES CARRINGTON", "BOARD CHAIR"),
        ("ELENA RUIZ", "SECRETARY"),
        ("MICHELLE ADEYEMI", "TREASURER"),
    ]:
        session.add(
            Person(
                ein="333333333", tax_year=2023, name=name, title=title,
                roles=["Board member"],
            )
        )
    session.commit()

    entry = build(session, "IL").organizations[0]
    assert entry.chief_executive is None
    assert any("none whose title says they run" in r for r in entry.reasons)


def test_an_unconfirmed_ein_is_named_as_the_reason(session) -> None:
    organization(session, "Delta Health", status=MatchStatus.PENDING)
    entry = build(session, "IL").organizations[0]
    assert entry.chief_executive is None
    assert any("review queue" in reason for reason in entry.reasons)


def test_an_organization_never_searched_says_so(session) -> None:
    organization(session, "Epsilon Health", status=None, ein=None)
    session.execute(select(Organization))
    entry = build(session, "IL").organizations[0]
    assert any("EIN never searched" in reason for reason in entry.reasons)


def test_a_confirmed_ein_with_no_filing_on_disk_says_so(session) -> None:
    organization(session, "Zeta Health", ein="444444444")
    entry = build(session, "IL").organizations[0]
    assert any("no Form 990 XML on this machine" in r for r in entry.reasons)


def test_no_web_address_is_distinguished_from_a_fruitless_crawl(session) -> None:
    no_site = organization(session, "Eta Health", ein="555555555")
    crawled = organization(
        session, "Theta Health", ein="666666666", website="https://theta.org"
    )
    session.add(
        WebsiteCrawl(
            organization_id=crawled.id, url="https://theta.org",
            outcome="no leadership page found",
        )
    )
    session.commit()

    entries = {e.name: e for e in build(session, "IL").organizations}
    assert any(
        "publishes no web address" in r for r in entries["Eta Health"].reasons
    )
    assert any(
        "no leadership page found" in r for r in entries["Theta Health"].reasons
    )


def test_a_website_name_is_used_and_labelled(session) -> None:
    org = organization(session, "Iota Health", ein="777777777", website="https://iota.org")
    session.add(
        WebsitePerson(
            organization_id=org.id, name="Grace Okoro",
            title="Chief Executive Officer", email="gokoro@iota.org",
            source_url="https://iota.org/leadership",
        )
    )
    session.commit()

    entry = build(session, "IL").organizations[0]
    assert entry.chief_executive == "Grace Okoro"
    assert entry.chief_executive_source == "Website"
    assert entry.chief_executive_email == "gokoro@iota.org"


def test_the_state_filter_actually_filters(session) -> None:
    organization(session, "Illinois Health", state="IL", ein="888888888")
    organization(session, "Wisconsin Health", state="WI", ein="999999999")

    assert build(session, "IL").total == 1
    assert build(session, "WI").total == 1
    assert build(session, None).total == 2


def test_the_rendered_report_names_the_gap_and_the_fix(session) -> None:
    org = organization(session, "Kappa Health", ein="121212121")
    session.add(
        Person(
            ein="121212121", tax_year=2023, name="LEE ADAMS",
            title="BOARD MEMBER", roles=["Board member"],
        )
    )
    session.commit()

    text = render(build(session, "IL"))

    assert "Chief executive coverage, IL" in text
    assert "No chief executive yet (1)" in text
    assert "Kappa Health" in text
    # The single largest free gain is stated once, not per row.
    assert "Uniform Data System" in text
    assert text.count("Uniform Data System") == 1


def test_an_empty_database_reports_nothing_rather_than_failing(session) -> None:
    report = build(session, "IL")
    assert report.total == 0
    assert "Organizations" in render(report)
