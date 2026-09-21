from tracker.report import compose, compose_html, subject

RANKED = [
    {"county": "Cook", "now": 900, "then": 1000, "change": -100, "pct": -10.0},
    {"county": "Adams", "now": 520, "then": 500, "change": 20, "pct": 4.0},
]


def test_body_states_data_month_and_coverage_caveat():
    body = compose("2026-08", 2253817, -6710, RANKED, [], [])
    assert "2026-08" in body
    assert "managed care" in body.lower()
    assert "Cook" in body


def test_problems_are_surfaced_not_hidden():
    body = compose("2026-08", 0, 0, [], [], ["Row sum does not match stated total"])
    assert "Row sum does not match" in body
    assert body.index("Row sum") < body.index("Data month")   # problems lead


def test_subject_flags_alerts():
    assert "ALERT" in subject("2026-08", -6710, ["Redetermination Report posted"])
    assert "ALERT" not in subject("2026-08", -6710, [])


def test_gainers_listed_largest_first():
    ranked = RANKED + [{"county": "Will", "now": 300, "then": 200, "change": 100, "pct": 50.0}]
    body = compose("2026-08", 1, 0, ranked, [], [])
    gain = body.split("COUNTIES GAINING")[1]
    assert gain.index("Will") < gain.index("Adams")


def test_baseline_line_included_when_given():
    b = {"baseline": "2026-01", "change": -64067, "pct": -2.8}
    assert "2026-01" in compose("2026-08", 2253817, -6710, RANKED, [], [], baseline=b)


# --- HTML body ---------------------------------------------------------------
# The HTML is an alternative rendering of the same report, never a different
# one: every figure in the text body must appear in it.

def test_html_carries_the_same_figures_as_text():
    b = {"baseline": "2026-01", "change": -64067, "pct": -2.8}
    html = compose_html("2026-08", 2253817, -6710, RANKED, [], [], baseline=b)
    for needle in ("2026-08", "2,253,817", "-6,710", "-64,067", "-2.8%",
                   "Cook", "1,000", "900", "-100", "-10.0%", "Adams", "+20", "+4.0%"):
        assert needle in html, needle
    assert "managed care only" in html.lower()
    assert "January 1, 2027" in html


def test_html_problems_lead_and_are_escaped():
    html = compose_html("2026-08", 0, 0, [], [], ["<script>x</script> row sum & total"])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp; total" in html
    assert html.index("DATA PROBLEMS") < html.index("Data month")


def test_html_alerts_are_escaped_and_shown():
    html = compose_html("2026-08", 1, 0, [], ['[report-center] new: <b>Report</b>'], [])
    assert "CLIFF WATCH" in html
    assert "<b>Report</b>" not in html and "&lt;b&gt;Report&lt;/b&gt;" in html


def test_html_gainers_largest_first_and_uses_display_names():
    ranked = RANKED + [{"county": "StClair", "now": 300, "then": 200, "change": 100, "pct": 50.0}]
    gain = compose_html("2026-08", 1, 0, ranked, [], []).split("COUNTIES GAINING")[1]
    assert gain.index("St. Clair") < gain.index("Adams")


def test_html_is_email_safe():
    html = compose_html("2026-08", 1, 0, RANKED, [], [])
    low = html.lower()
    assert "<table" in low
    for banned in ("<style", "<script", "<link", "class="):
        assert banned not in low, banned
    # The routine reads this file back before sending; file readers truncate
    # very long lines, which would silently cut rows out of the email.
    assert max(len(line) for line in html.splitlines()) < 1000


def test_county_counts_in_both_bodies():
    ranked = RANKED + [{"county": "Will", "now": 5, "then": 5, "change": 0, "pct": 0.0}]
    line = "1 declined, 1 gained, 1 flat, of 3"
    assert line in compose("2026-08", 1, 0, ranked, [], [])
    assert line in compose_html("2026-08", 1, 0, ranked, [], [])


# --- Header: what this is and who sends it -----------------------------------

def test_both_bodies_open_with_title_byline_and_about():
    for body in (compose("2026-08", 1, 0, RANKED, [], []),
                 compose_html("2026-08", 1, 0, RANKED, [], [])):
        assert "Illinois Medicaid Cliff Tracker" in body
        assert "Brought to you by ALLSTAR Partners" in body
        assert "102 Illinois counties" in body
        assert body.index("Illinois Medicaid Cliff Tracker") < body.index("Data month")


def test_brand_name_casing_is_never_shouted():
    """Only ALLSTAR is capitalised, and CSS must not uppercase the byline."""
    html = compose_html("2026-08", 1, 0, RANKED, [], [])
    for body in (compose("2026-08", 1, 0, RANKED, [], []), html):
        assert "ALLSTAR PARTNERS" not in body and "Allstar" not in body
    byline = html[html.index("Brought to you by") - 200:html.index("Brought to you by")]
    assert "uppercase" not in byline.split("<div")[-1]


def test_problems_still_precede_every_figure_with_header_present():
    for fn in (compose, compose_html):
        body = fn("2026-08", 2253817, -6710, RANKED, [], ["Row sum does not match"])
        assert body.index("Row sum") < body.index("2,253,817")
