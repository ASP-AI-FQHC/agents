"""The profile page's own logic: headline movement, merging people, grouping
vendors, and the rules about what may and may not be shown."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.formatting import person_name, relative_date, signed_money, signed_percent
from app.models import Filing, FilingProfile, ProgramArea
from app.queries import (
    grouped_contractors,
    headline_figures,
    profile_people,
    vendor_category,
)


def filing(year, **kwargs):
    return Filing(ein="363197647", tax_year=year, **kwargs)


# ---------------------------------------------------------------------------
# Headline cards
# ---------------------------------------------------------------------------


def test_movement_is_measured_against_the_previous_filed_year() -> None:
    cards = headline_figures(
        [
            filing(2023, total_revenue=28_100_000, total_expenses=33_800_000),
            filing(2022, total_revenue=31_100_000, total_expenses=30_400_000),
        ]
    )
    revenue, expenses = cards[0], cards[1]

    assert revenue.change == -3_000_000
    assert revenue.direction == -1
    assert round(revenue.change_share * 100, 1) == -9.6
    assert revenue.previous_year == 2022

    assert expenses.direction == 1
    assert expenses.change == 3_400_000


def test_a_single_year_shows_no_movement_at_all() -> None:
    """One filed year is not evidence of stability."""
    card = headline_figures([filing(2023, total_revenue=28_100_000)])[0]
    assert card.change is None
    assert card.change_share is None
    assert card.direction == 0


def test_a_missing_prior_figure_yields_no_movement() -> None:
    cards = headline_figures(
        [filing(2023, total_assets=40_100_000), filing(2022, total_revenue=1)]
    )
    assets = next(card for card in cards if card.label == "Assets")
    assert assets.value == 40_100_000
    assert assets.change is None


def test_a_prior_year_of_zero_gives_an_amount_but_no_percentage() -> None:
    cards = headline_figures(
        [filing(2023, total_revenue=500_000), filing(2022, total_revenue=0)]
    )
    assert cards[0].change == 500_000
    assert cards[0].change_share is None  # dividing by zero is not "infinite growth"


def test_the_xml_profile_only_fills_gaps_in_its_own_tax_year() -> None:
    profile = FilingProfile(
        ein="363197647", tax_year=2023, total_liabilities=8_000_000
    )
    cards = headline_figures([filing(2023, total_revenue=28_100_000)], profile)
    liabilities = next(card for card in cards if card.label == "Liabilities")
    assert liabilities.value == 8_000_000

    stale = FilingProfile(ein="363197647", tax_year=2019, total_liabilities=1)
    cards = headline_figures([filing(2023, total_revenue=28_100_000)], stale)
    liabilities = next(card for card in cards if card.label == "Liabilities")
    assert liabilities.value is None


def test_government_grants_are_not_double_counted_with_contributions() -> None:
    row = filing(
        2023,
        total_revenue=28_100_000,
        contributions=16_000_000,
        government_grants=15_000_000,
        program_service_revenue=11_000_000,
    )
    components = dict(row.revenue_components())
    assert components["Government grants"] == 15_000_000
    assert components["Other contributions"] == 1_000_000
    assert "Contributions and grants" not in components
    # The components sum to the reported contributions, not to double them.
    assert (
        components["Government grants"] + components["Other contributions"]
        == 16_000_000
    )


def test_contributions_alone_are_shown_as_filed() -> None:
    row = filing(2023, contributions=16_000_000)
    assert dict(row.revenue_components()) == {"Contributions and grants": 16_000_000}


def test_no_residual_component_is_invented_to_reach_the_total() -> None:
    row = filing(2023, total_revenue=28_100_000, government_grants=15_000_000)
    assert row.revenue_components() == [("Government grants", 15_000_000)]


def test_derived_figures_need_both_of_their_inputs() -> None:
    assert filing(2023, total_assets=40.0, total_liabilities=8.0).net_assets == 32.0
    assert filing(2023, total_assets=40.0).net_assets is None
    assert filing(2023, total_revenue=5.0).surplus is None
    assert filing(2023, total_revenue=5.0, total_expenses=4.0).surplus == 1.0


def test_no_filings_means_no_cards_rather_than_empty_ones() -> None:
    assert headline_figures([]) == []


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def a_filing_person(name, title=None, roles=(), compensation=None, year=2023):
    return SimpleNamespace(
        name=name,
        title=title,
        roles=list(roles),
        average_hours=None,
        total_compensation=compensation,
        tax_year=year,
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_board_member=any(
            role in ("Board member", "Institutional trustee") for role in roles
        ),
    )


def a_website_person(name, title=None, email=None, board=False):
    return SimpleNamespace(
        name=name,
        title=title,
        email=email,
        source_url="https://example.org/leadership",
        fetched_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        is_board_member=board,
    )


def test_the_same_person_from_two_sources_becomes_one_row() -> None:
    staff, _ = profile_people(
        [a_filing_person("MARIA T ALVAREZ", "CEO", compensation=410_000)],
        [a_website_person("Maria Alvarez", email="malvarez@example.org")],
    )
    assert len(staff) == 1
    row = staff[0]
    assert row.sources == ["Form 990", "Website"]
    assert row.compensation == 410_000       # from the filing
    assert row.email == "malvarez@example.org"  # from the page


def test_the_filing_wins_where_the_two_sources_disagree() -> None:
    staff, _ = profile_people(
        [a_filing_person("ANA RUIZ", "CHIEF EXECUTIVE OFFICER")],
        [a_website_person("Ana Ruiz", title="Interim Director")],
    )
    assert staff[0].title == "CHIEF EXECUTIVE OFFICER"


def test_people_who_merely_share_a_first_name_stay_separate() -> None:
    staff, _ = profile_people(
        [a_filing_person("ANA RUIZ"), a_filing_person("ANA MORALES")], []
    )
    assert len(staff) == 2


def test_board_members_are_listed_apart_from_staff() -> None:
    staff, board = profile_people(
        [
            a_filing_person("ANA RUIZ", roles=["Officer"], compensation=300_000),
            a_filing_person("LUIS GOMEZ", roles=["Board member"]),
        ],
        [],
    )
    assert [p.name for p in staff] == ["ANA RUIZ"]
    assert [p.name for p in board] == ["LUIS GOMEZ"]


def test_staff_are_ordered_by_what_they_are_paid() -> None:
    staff, _ = profile_people(
        [
            a_filing_person("JUNIOR STAFFER", compensation=120_000),
            a_filing_person("THE CHIEF EXECUTIVE", compensation=410_000),
        ],
        [],
    )
    assert staff[0].name == "THE CHIEF EXECUTIVE"


def test_the_uds_director_carries_a_direct_line_the_990_never_has() -> None:
    uds = SimpleNamespace(
        director_name="Grace Okoro",
        director_email="gokoro@example.org",
        director_phone="(312) 555-0100",
        year=2025,
        fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    staff, _ = profile_people([], [], uds)
    assert staff[0].title == "Project Director"
    assert staff[0].phone == "(312) 555-0100"
    assert staff[0].sources == ["HRSA UDS"]
    assert staff[0].as_of_label == "2025"


def test_every_row_says_where_it_came_from() -> None:
    staff, board = profile_people(
        [a_filing_person("ANA RUIZ", roles=["Board member"])],
        [a_website_person("Tom Blake", title="CIO")],
    )
    assert all(person.sources for person in staff + board)


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "services, expected",
    [
        ("Electronic health record hosting", "Health IT and software"),
        ("IT SERVICES AND SUPPORT", "Health IT and software"),
        ("Medical billing and coding", "Billing and revenue cycle"),
        ("Independent audit services", "Audit and accounting"),
        ("Locum tenens physician staffing", "Clinical staffing and services"),
        ("Building renovation", "Facilities and construction"),
        ("Employee benefits broker", "Insurance and benefits"),
        ("Management consulting", "Consulting and management"),
        ("Catering", "Other services"),
        (None, "Not described"),
        ("   ", "Not described"),
    ],
)
def test_vendors_are_grouped_by_what_the_filing_says_they_do(
    services, expected
) -> None:
    assert vendor_category(services) == expected


def test_vendor_groups_lead_with_the_largest_spend() -> None:
    contractors = [
        SimpleNamespace(name="Small IT Co", services="IT services", compensation=150_000),
        SimpleNamespace(name="Big Clinical", services="Nursing staff", compensation=900_000),
        SimpleNamespace(name="Big IT Co", services="EHR hosting", compensation=400_000),
    ]
    groups = grouped_contractors(contractors)
    assert groups[0][0] == "Clinical staffing and services"
    assert [c.name for c in groups[1][1]] == ["Big IT Co", "Small IT Co"]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_a_change_carries_its_own_sign() -> None:
    assert signed_money(-3_000_000) == "-$3.0M"
    assert signed_money(240_000) == "+$240K"
    assert signed_money(0) == "no change"
    assert signed_money(None) == "Not available"
    assert signed_percent(-0.092) == "-9.2%"
    assert signed_percent(0.114) == "+11.4%"


def test_dates_are_phrased_the_way_a_person_would() -> None:
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    assert relative_date(datetime(2026, 8, 29, tzinfo=timezone.utc), now=now) == "today"
    assert relative_date(datetime(2026, 8, 28, tzinfo=timezone.utc), now=now) == "yesterday"
    assert relative_date(datetime(2026, 8, 20, tzinfo=timezone.utc), now=now) == "last week"
    assert relative_date(datetime(2026, 6, 1, tzinfo=timezone.utc), now=now) == "2 months ago"
    assert relative_date(datetime(2021, 6, 1, tzinfo=timezone.utc), now=now) == "June 2021"
    assert relative_date(None) == "Not available"


def test_names_filed_in_capitals_are_made_readable() -> None:
    assert person_name("BERNEICE MILLS-THOMAS") == "Berneice Mills-Thomas"
    assert person_name("JOHN MCDONALD III") == "John McDonald III"
    assert person_name("SEAN O'BRIEN MD") == "Sean O'Brien MD"
    assert person_name("R J SMITH JR") == "R J Smith Jr"


def test_a_name_already_cased_is_left_exactly_as_the_source_wrote_it() -> None:
    assert person_name("Ada Nwosu") == "Ada Nwosu"
    assert person_name("van Beethoven") == "van Beethoven"
    assert person_name("") == "Not available"


# ---------------------------------------------------------------------------
# Program areas
# ---------------------------------------------------------------------------


def test_a_program_gets_a_readable_title_from_its_narrative() -> None:
    program = ProgramArea(
        ein="1",
        tax_year=2023,
        description="Dental services. Provided at four sites to 12,000 patients.",
    )
    assert program.title == "Dental services"


def test_a_program_with_no_narrative_has_no_title() -> None:
    assert ProgramArea(ein="1", tax_year=2023).title is None


def test_a_programs_net_cost_needs_both_figures() -> None:
    assert ProgramArea(ein="1", tax_year=2023, expenses=10.0, revenue=4.0).net_cost == 6.0
    assert ProgramArea(ein="1", tax_year=2023, expenses=10.0).net_cost is None


def test_titles_filed_in_capitals_are_made_readable() -> None:
    from app.formatting import job_title

    assert job_title("PRESIDENT AND CEO") == "President and CEO"
    assert job_title("CHIEF MEDICAL OFFICER") == "Chief Medical Officer"
    assert job_title("SECRETARY/TREASURER") == "Secretary/Treasurer"
    assert job_title("VICE-PRESIDENT OF FINANCE") == "Vice-President of Finance"
    assert job_title("DIRECTOR OF IT") == "Director of IT"
    assert job_title("CHIEF INFORMATION OFFICER (CIO)") == (
        "Chief Information Officer (CIO)"
    )


def test_a_title_already_cased_is_left_as_written() -> None:
    from app.formatting import job_title

    assert job_title("Director of Human Resources") == "Director of Human Resources"
    assert job_title(None) == "Not available"


# ---------------------------------------------------------------------------
# Parse records and ORM rows must agree on their properties
# ---------------------------------------------------------------------------


def test_the_stored_row_answers_every_question_the_parse_record_does() -> None:
    """A property on one and not the other makes a section vanish in silence.

    Jinja treats an undefined attribute as merely falsy rather than raising, so
    ``{% if profile.has_expense_split %}`` against a model lacking that property
    renders nothing and reports nothing. This is checked rather than
    remembered.
    """
    from pipeline.irs import OrganizationFacts

    record_properties = {
        name
        for name, value in vars(OrganizationFacts).items()
        if isinstance(value, property)
    }
    stored_properties = {
        name
        for name, value in vars(FilingProfile).items()
        if isinstance(value, property)
    }
    assert record_properties <= stored_properties, (
        "FilingProfile is missing: "
        + ", ".join(sorted(record_properties - stored_properties))
    )


def test_the_expense_split_is_visible_when_it_was_reported() -> None:
    profile = FilingProfile(
        ein="1", tax_year=2023, total_expenses=33_800_000,
        program_expenses=29_100_000, management_expenses=4_400_000,
        fundraising_expenses=300_000,
    )
    assert profile.has_expense_split
    assert profile.expense_components()[0] == ("Program services", 29_100_000)
    assert round(profile.program_expense_share, 3) == 0.861

    assert not FilingProfile(ein="1", tax_year=2023).has_expense_split
