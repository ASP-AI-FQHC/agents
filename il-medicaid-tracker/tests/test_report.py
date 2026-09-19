from tracker.report import compose, subject

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
