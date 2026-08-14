"""Program areas, funding composition, update history and similar organizations.

The theme running through these is the same as everywhere else: a data point we
cannot source is reported as unavailable rather than approximated.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    ChangeEvent,
    ChangeKind,
    EinMatch,
    Filing,
    GranteeType,
    MatchStatus,
    Organization,
    Score,
)
from app.ntee import CODES, MAJOR_GROUPS, describe, label
from app.queries import organization_changes, similar_organizations
from pipeline.hrsa import deduplicate, merge_awardees, parse_awardees, parse_sites
from pipeline.propublica import parse_filings
from tests.test_hrsa import AWARDEES_CSV, SITES_CSV


# ---------------------------------------------------------------------------
# Program areas
# ---------------------------------------------------------------------------


def test_every_hrsa_funding_stream_is_kept() -> None:
    """A health center commonly holds several awards; each is a program area."""
    records, _, _ = parse_sites(SITES_CSV)
    organizations = deduplicate(records)
    merge_awardees(organizations, parse_awardees(AWARDEES_CSV)[0])

    erie = next(o for o in organizations if o.name.startswith("Erie"))
    assert erie.funding_programs == [
        "Community Health Center Program",
        "Health Care for the Homeless",
    ]
    # The single-value field still holds the primary programme.
    assert erie.funding_program == "Community Health Center Program"


def test_organizations_without_awards_have_no_programmes() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    organizations = deduplicate(records)
    merge_awardees(organizations, parse_awardees(AWARDEES_CSV)[0])

    milwaukee = next(o for o in organizations if o.name.startswith("Milwaukee"))
    assert milwaukee.funding_programs == []


def test_known_ntee_codes_are_described() -> None:
    specific, group = describe("E320")
    assert specific == "Ambulatory health center / community clinic"
    assert group == "Health care"


def test_unknown_ntee_codes_fall_back_to_the_major_group() -> None:
    """Inventing a description for an unlisted code would be a fabrication."""
    specific, group = describe("E999")
    assert specific is None
    assert group == "Health care"
    assert label("E999") == "Health care"


def test_unrecognized_ntee_yields_nothing_rather_than_a_guess() -> None:
    assert describe("1234") == (None, None)
    assert describe("") == (None, None)
    assert describe(None) == (None, None)
    assert label(None) is None


def test_ntee_tables_are_internally_consistent() -> None:
    """Every specific code must sit under a major group we can name."""
    for code in CODES:
        assert code[0] in MAJOR_GROUPS, code
    assert len(MAJOR_GROUPS) == 26


# ---------------------------------------------------------------------------
# Funding sources
# ---------------------------------------------------------------------------


def test_revenue_composition_is_parsed_when_present() -> None:
    payload = {
        "filings_with_data": [
            {
                "tax_prd_yr": 2023,
                "tax_prd": 202312,
                "totrevenue": 20_000_000,
                "totcntrbgfts": 12_000_000,
                "totprgmrevnue": 7_500_000,
                "invstmntinc": 500_000,
                "formtype": 0,
            }
        ]
    }
    filing = parse_filings("362167869", payload)[0]

    assert filing.total_revenue == 20_000_000
    assert filing.contributions == 12_000_000
    assert filing.program_service_revenue == 7_500_000
    assert filing.investment_income == 500_000


def test_program_service_revenue_is_never_used_as_total_revenue() -> None:
    """They are different quantities: treating one as the other would understate
    an organization by the size of its grants."""
    payload = {
        "filings_with_data": [
            {"tax_prd_yr": 2023, "tax_prd": 202312, "totprgmrevnue": 7_500_000}
        ]
    }
    filing = parse_filings("362167869", payload)[0]

    assert filing.total_revenue is None
    assert filing.program_service_revenue == 7_500_000


def test_missing_composition_stays_unavailable() -> None:
    payload = {
        "filings_with_data": [
            {"tax_prd_yr": 2023, "tax_prd": 202312, "totrevenue": 20_000_000}
        ]
    }
    filing = parse_filings("362167869", payload)[0]

    assert filing.contributions is None
    assert filing.has_composition is False


def test_contribution_share_needs_both_figures(session: Session) -> None:
    with_both = Filing(
        ein="1", tax_year=2023, total_revenue=20_000_000, contributions=12_000_000
    )
    without = Filing(ein="2", tax_year=2023, total_revenue=20_000_000)
    zero_revenue = Filing(ein="3", tax_year=2023, total_revenue=0, contributions=100)

    assert with_both.contribution_share == pytest.approx(0.6)
    assert without.contribution_share is None
    assert zero_revenue.contribution_share is None


# ---------------------------------------------------------------------------
# Similar organizations
# ---------------------------------------------------------------------------


def add_org(
    session: Session,
    name: str,
    *,
    state: str = "IL",
    sites: int = 10,
    revenue: float | None = None,
    ntee: str | None = "E320",
    ein: str | None = None,
) -> Organization:
    org = Organization(
        dedup_key=f"{name.lower()}|{state}",
        name=name,
        normalized_name=name.lower(),
        state=state,
        city="Chicago",
        site_count=sites,
        ntee_code=ntee,
        grantee_type=GranteeType.AWARDEE,
    )
    session.add(org)
    session.flush()
    session.add(Score(organization_id=org.id, composite=70.0, breakdown=[]))
    if ein:
        session.add(
            EinMatch(organization_id=org.id, ein=ein, score=99.0, status=MatchStatus.AUTO)
        )
        if revenue is not None:
            session.add(Filing(ein=ein, tax_year=2023, total_revenue=revenue))
    session.commit()
    return org


def test_similar_organizations_are_ranked_by_resemblance(session: Session) -> None:
    target = add_org(session, "Erie Family Health", sites=12, revenue=20e6, ein="111111111")
    add_org(session, "Near Twin", sites=11, revenue=22e6, ein="222222222")
    add_org(session, "Different State", state="TX", sites=12, revenue=20e6, ein="333333333")
    add_org(session, "Tiny Clinic", sites=1, revenue=500_000, ein="444444444")

    similar = similar_organizations(session, target)

    assert similar
    assert similar[0].row.organization.name == "Near Twin"
    assert "also in IL" in similar[0].reasons
    assert "similar revenue" in similar[0].reasons
    names = [s.row.organization.name for s in similar]
    # A one-site clinic shares Erie's state and IRS code but is not a peer, and
    # an out-of-state twin is a different market.
    assert "Tiny Clinic" not in names
    assert "Different State" not in names


def test_an_organization_is_never_similar_to_itself(session: Session) -> None:
    target = add_org(session, "Erie Family Health")
    add_org(session, "Another Center")

    similar = similar_organizations(session, target)
    assert all(s.row.organization.id != target.id for s in similar)


def test_no_close_match_yields_nothing_rather_than_a_weak_one(
    session: Session,
) -> None:
    """A list of unrelated organizations is worse than an empty one."""
    target = add_org(session, "Erie Family Health", sites=12)
    add_org(session, "Unrelated", state="TX", sites=1, ntee="B999")

    assert similar_organizations(session, target) == []


def test_similar_list_respects_the_limit(session: Session) -> None:
    target = add_org(session, "Erie Family Health", sites=10)
    for index in range(8):
        add_org(session, f"Peer {index}", sites=10)

    assert len(similar_organizations(session, target, limit=3)) == 3


# ---------------------------------------------------------------------------
# Update history
# ---------------------------------------------------------------------------


def test_organization_history_is_scoped_and_newest_first(session: Session) -> None:
    first = add_org(session, "Erie Family Health")
    second = add_org(session, "Milwaukee Health")

    session.add_all(
        [
            ChangeEvent(
                organization_id=first.id,
                kind=ChangeKind.SITES,
                summary="Opened 2 delivery sites (8 to 10)",
                current_value="10",
            ),
            ChangeEvent(
                organization_id=first.id,
                kind=ChangeKind.AWARD,
                summary="Federal award increased 10%",
            ),
            ChangeEvent(
                organization_id=second.id,
                kind=ChangeKind.SITES,
                summary="Closed 1 delivery site (4 to 3)",
            ),
        ]
    )
    session.commit()

    history = organization_changes(session, first.id)

    assert len(history) == 2
    assert history[0].kind == ChangeKind.AWARD  # newest first
    assert all(event.organization_id == first.id for event in history)


def test_history_is_empty_before_a_second_run(session: Session) -> None:
    org = add_org(session, "Erie Family Health")
    assert organization_changes(session, org.id) == []


def test_persisted_filings_expose_the_composition_flag(session: Session) -> None:
    """The templates read this off database rows, not parse-time records, and a
    property missing from the model fails silently in Jinja."""
    session.add(
        Filing(
            ein="362167869",
            tax_year=2023,
            total_revenue=20_000_000,
            contributions=12_000_000,
        )
    )
    session.add(Filing(ein="999999999", tax_year=2023, total_revenue=1_000_000))
    session.commit()

    session.expire_all()
    from sqlalchemy import select

    rows = {f.ein: f for f in session.scalars(select(Filing)).all()}
    assert rows["362167869"].has_composition is True
    assert rows["999999999"].has_composition is False
    assert rows["362167869"].contribution_share == pytest.approx(0.6)
