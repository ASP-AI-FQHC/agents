"""Schema behaviour: relationships, cascades, and the EIN trust rule."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    EinMatch,
    Filing,
    GranteeType,
    MatchStatus,
    Organization,
    Score,
    Site,
)


def make_org(session: Session, **overrides) -> Organization:
    org = Organization(
        dedup_key=overrides.pop("dedup_key", "erie family health center|IL"),
        name=overrides.pop("name", "Erie Family Health Center"),
        normalized_name=overrides.pop("normalized_name", "erie family health center"),
        state=overrides.pop("state", "IL"),
        site_count=overrides.pop("site_count", 13),
        grantee_type=overrides.pop("grantee_type", GranteeType.AWARDEE),
        **overrides,
    )
    session.add(org)
    session.commit()
    return org


def test_organization_persists_with_sites(session: Session) -> None:
    org = make_org(session)
    session.add_all(
        [
            Site(organization_id=org.id, site_id="S1", name="Humboldt Park", state="IL"),
            Site(organization_id=org.id, site_id="S2", name="West Town", state="IL"),
        ]
    )
    session.commit()

    stored = session.get(Organization, org.id)
    assert len(stored.sites) == 2
    assert stored.grantee_type == GranteeType.AWARDEE


def test_dedup_key_is_unique(session: Session) -> None:
    make_org(session)
    with pytest.raises(IntegrityError):
        make_org(session, name="Erie Family Health Center Inc")


def test_duplicate_site_id_for_same_org_is_rejected(session: Session) -> None:
    org = make_org(session)
    session.add(Site(organization_id=org.id, site_id="S1", name="A"))
    session.commit()
    session.add(Site(organization_id=org.id, site_id="S1", name="A duplicate"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_organization_cascades(session: Session) -> None:
    org = make_org(session)
    session.add_all(
        [
            Site(organization_id=org.id, site_id="S1", name="A"),
            EinMatch(organization_id=org.id, ein="362167869", status=MatchStatus.AUTO),
            Score(organization_id=org.id, composite=88.0, breakdown=[]),
        ]
    )
    session.commit()

    session.delete(org)
    session.commit()

    assert session.scalars(select(Site)).all() == []
    assert session.scalars(select(EinMatch)).all() == []
    assert session.scalars(select(Score)).all() == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (MatchStatus.AUTO, True),
        (MatchStatus.ACCEPTED, True),
        (MatchStatus.PENDING, False),
        (MatchStatus.REJECTED, False),
        (MatchStatus.UNMATCHED, False),
    ],
)
def test_ein_is_only_exposed_for_trusted_matches(
    session: Session, status: MatchStatus, expected: bool
) -> None:
    """A pending or rejected match must never be treated as a confirmed EIN."""
    org = make_org(session)
    session.add(EinMatch(organization_id=org.id, ein="362167869", status=status))
    session.commit()
    session.refresh(org)

    assert (org.ein == "362167869") is expected


def test_one_filing_per_ein_and_year(session: Session) -> None:
    session.add(Filing(ein="362167869", tax_year=2023, total_revenue=100.0))
    session.commit()
    session.add(Filing(ein="362167869", tax_year=2023, total_revenue=200.0))
    with pytest.raises(IntegrityError):
        session.commit()


def test_missing_financials_stay_null(session: Session) -> None:
    """Absent figures must persist as NULL -- never coerced to zero."""
    session.add(Filing(ein="362167869", tax_year=2022))
    session.commit()

    filing = session.scalars(select(Filing)).one()
    assert filing.total_revenue is None
    assert filing.total_expenses is None
    assert filing.total_assets is None
