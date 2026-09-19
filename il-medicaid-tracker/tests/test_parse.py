from tracker.parse import parse_text

SIMPLE = """
        FIDE-SNP Enrollments for August 2026
 County       Aetna    Molina   Grand Total
 Adams          179       211           578
 Cook        12,661     3,711        36,652
 Grand Total 12,840     3,922        37,230
"""

ORPHANED = """
        MMAI Enrollments- April 2025
 County       Aetna    Molina   County Total
 Adams          104       234           537
 Grand Total    104       234           537
 Bureau          80        73           230
        Health Choice of IL Enrollments- April 2025 (continued)
 Cook         1,000     2,000         3,000
 Grand Total  1,080     2,073         3,230
"""


def test_parses_counties_and_total():
    secs = parse_text(SIMPLE)
    (prog, period), sec = next(iter(secs.items()))
    assert prog == "dual"
    assert period == "August 2026"
    assert sec["counties"] == {"Adams": 578, "Cook": 36652}
    assert sec["total"] == 37230


def test_orphaned_rows_claimed_by_next_header():
    secs = parse_text(ORPHANED)
    hc = secs[("healthchoice", "April 2025")]
    assert hc["counties"]["Bureau"] == 230   # orphan, back-assigned
    assert hc["counties"]["Cook"] == 3000
    assert secs[("dual", "April 2025")]["counties"] == {"Adams": 537}


def test_duplicate_county_recorded_as_conflict():
    """A county appearing twice inside one open section means rows from a
    different program bled in after a page break. Must be flagged, not averaged."""
    dupe = """
        FIDE-SNP Enrollments for August 2026
 County       Aetna    Molina   Grand Total
 Adams          179       211           578
 Adams            1         2           999
 Grand Total 12,840     3,922        37,230
"""
    sec = parse_text(dupe)[("dual", "August 2026")]
    assert "Adams" in sec["conflicts"]


def test_repeated_identical_row_is_not_a_conflict():
    """Some files restate a row verbatim across a page break; same value is benign."""
    same = """
        FIDE-SNP Enrollments for August 2026
 Adams          179       211           578
 Adams          179       211           578
 Grand Total 12,840     3,922        37,230
"""
    assert parse_text(same)[("dual", "August 2026")]["conflicts"] == []
