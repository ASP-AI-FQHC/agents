"""Auditing a third-party spreadsheet before any of it reaches the database.

Built after a 1,799-row FQHC file arrived in which the email column was the
organization's name truncated to twenty characters, 58% of the CEO names were
the literal string "Contact Organization", and the clinic count was a uniform
random integer. Every check here is one that file failed.
"""

from __future__ import annotations

import random

import pytest

from pipeline.review_import import (
    flatness,
    looks_constructed,
    name_matches_email,
    render,
    review,
)


def write(tmp_path, name, header, rows):
    import csv

    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# Constructed addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "email, organization",
    [
        # The real file. None of these domains exists.
        ("info@howardbrownheal.org", "Howard Brown Health Center"),
        ("info@belovedcommunit.org", "Beloved Community Family Wellness Center"),
        ("info@tapestry360heal.org", "Tapestry 360 Health"),
        ("info@lawndalechristi.org", "Lawndale Christian Health Center"),
    ],
)
def test_an_address_built_from_the_name_is_recognised(email, organization) -> None:
    assert looks_constructed(email, organization)


@pytest.mark.parametrize(
    "email, organization",
    [
        # A real generic mailbox sits on the organization's real domain.
        ("info@howardbrown.org", "Howard Brown Health Center"),
        ("info@nearnorthhealth.org", "Near North Health Service Corporation"),
        # A named person is not a constructed generic address.
        ("bmills@howardbrownheal.org", "Howard Brown Health Center"),
        # An unrelated domain is a different problem, not this one.
        ("info@example.com", "Howard Brown Health Center"),
    ],
)
def test_a_real_address_is_not_flagged_as_constructed(email, organization) -> None:
    assert not looks_constructed(email, organization)


# ---------------------------------------------------------------------------
# Addresses that belong to somebody else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "person, email",
    [
        ("Travis Gayles", "ahmedc@howardbrown.org"),
        ("Ryan Gadia", "dfulwiler@esperanzachicago.org"),
        ("Mahomed Ouedraogo", "thea.kachorisflores@achn.net"),
    ],
)
def test_a_mismatched_contact_is_caught(person, email) -> None:
    """Each of these pairs a real-looking address with the wrong name."""
    assert name_matches_email(person, email) is False


@pytest.mark.parametrize(
    "person, email",
    [
        ("Berneice Mills-Thomas", "bmillsthomas@nearnorth.org"),
        ("Grace Okoro", "gokoro@example.org"),
        ("Daniel Fulwiler", "dfulwiler@esperanzachicago.org"),
        ("Linnea Windel", "lwindel@vnahealth.org"),
        ("Kiran Siddiqui", "ksiddiqui@hamdardhealth.org"),
    ],
)
def test_a_matching_contact_passes(person, email) -> None:
    assert name_matches_email(person, email) is True


def test_no_judgement_without_enough_to_judge() -> None:
    assert name_matches_email("", "someone@example.org") is None
    assert name_matches_email("Grace Okoro", "") is None


# ---------------------------------------------------------------------------
# Generated numeric columns
# ---------------------------------------------------------------------------


def test_a_uniform_random_column_is_recognised() -> None:
    """The shape of the real file's Clinics column.

    Dealt round-robin rather than drawn independently, because that is what the
    file actually showed: 118, 118, 117, 117, 116, 116 ... across thirteen
    values. An independent draw of the same size is noisier than this and is
    deliberately *not* condemned -- the check is for a column too even to be
    real, not merely for one that is broadly spread.
    """
    values = [(n % 20) + 1 for n in range(1799)]

    measured = flatness(values)
    assert measured is not None
    ratio, spread = measured
    assert ratio < 1.3
    assert spread >= 6


def test_a_real_world_count_is_not_flagged() -> None:
    """Health center site counts are heavily skewed toward the low end.

    The check that keeps this useful: it must not condemn a genuine column.
    """
    values = [1] * 400 + [2] * 260 + [3] * 170 + [4] * 110 + [5] * 70 + [6] * 40
    values += [7] * 25 + [8] * 15 + [12] * 8 + [20] * 3

    measured = flatness(values)
    assert measured is None or measured[0] > 1.5


def test_too_little_data_yields_no_verdict() -> None:
    assert flatness([1, 2, 3]) is None
    assert flatness([3] * 100) is None


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

HEADER = [
    "Name", "Website", "Email", "City", "State",
    "CEO Name", "CEO Email", "Employees", "Clinics", "Annual Revenue",
    "Founded Year",
]


def test_the_report_names_every_kind_of_problem(tmp_path) -> None:
    random.seed(3)
    rows = [
        [
            f"Health Center {n}",
            f"https://hc{n}.org",
            f"info@healthcenter{n % 7}.org",
            "Chicago",
            "IL",
            "Contact Organization" if n % 2 else "Grace Okoro",
            f"someoneelse@hc{n}.org",
            0 if n % 8 == 0 else 200,
            (n % 20) + 1,               # dealt evenly, as the real file was
            (n % 40 + 1) * 1_000_000,
            "2000",
        ]
        for n in range(300)
    ]
    path = write(tmp_path, "third-party.csv", HEADER, rows)

    result = review(path, session=None)
    text = render(result)

    assert result.total == 300
    assert "Clinics" in text and "generated" in text
    assert "report zero" in text
    assert "Founded Year" in text
    assert "placeholder" in " ".join(
        problem for row in result.rows for problem in row.problems
    )
    assert "Nothing has been written to the database" in text


def test_duplicates_inside_the_file_are_reported(tmp_path) -> None:
    rows = [
        ["Erie Family Health Center", "", "", "Chicago", "IL", "", "", "", "", "", ""],
        ["ERIE FAMILY HEALTH CENTER", "", "", "Chicago", "IL", "", "", "", "", "", ""],
        ["Near North Health", "", "", "Chicago", "IL", "", "", "", "", "", ""],
    ]
    path = write(tmp_path, "dupes.csv", HEADER, rows)

    result = review(path, session=None)

    assert result.count("duplicate in file") == 1
    assert result.rows[1].duplicate_of_line == 2


def test_a_file_with_no_name_column_is_refused_rather_than_guessed_at(tmp_path) -> None:
    path = write(tmp_path, "wrong.csv", ["Foo", "Bar"], [["a", "b"]])

    result = review(path, session=None)

    assert result.missing_columns == ["name"]
    assert "No organization-name column" in render(result)


def test_a_clean_file_produces_no_complaints(tmp_path) -> None:
    """The audit has to be able to say yes, or it says nothing."""
    rows = [
        [
            "Near North Health Service Corporation",
            "https://nearnorthhealth.org",
            "info@nearnorthhealth.org",
            "Chicago", "IL",
            "Berneice Mills-Thomas",
            "bmillsthomas@nearnorthhealth.org",
            "262", "8", "28137442", "1982",
        ],
        [
            "Erie Family Health Centers",
            "https://eriefamilyhealth.org",
            "info@eriefamilyhealth.org",
            "Chicago", "IL",
            "Lee Francis",
            "lfrancis@eriefamilyhealth.org",
            "762", "13", "94217883", "1957",
        ],
    ]
    path = write(tmp_path, "clean.csv", HEADER, rows)

    result = review(path, session=None)

    assert len(result.trustworthy) == 2
    assert result.column_findings == []


def test_matching_against_the_database_answers_the_duplicate_question(session) -> None:
    """The actual ask: do not add a health center that is already here."""
    import csv
    from pathlib import Path
    import tempfile

    from app.models import GranteeType, Organization

    session.add(
        Organization(
            dedup_key="near north|il",
            name="Near North Health Service Corporation",
            normalized_name="near north health service",
            state="IL",
            grantee_type=GranteeType.AWARDEE,
        )
    )
    session.commit()

    directory = Path(tempfile.mkdtemp())
    path = directory / "rows.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerow(
            ["Near North Health Service Corporation", "", "", "Chicago", "IL",
             "", "", "", "", "", ""]
        )
        writer.writerow(
            ["Brand New Health Center", "", "", "Chicago", "IL",
             "", "", "", "", "", ""]
        )

    result = review(path, session)

    assert result.count("already in database") == 1
    assert result.count("new") == 1
    assert result.rows[0].matched_name == "Near North Health Service Corporation"
