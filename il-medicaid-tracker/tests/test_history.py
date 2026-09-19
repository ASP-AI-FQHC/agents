import os
import tempfile

from tracker.history import load, record, months, series, save, to_key


def test_records_and_dedupes():
    h = load(None)
    assert record(h, "healthchoice", "August 2026", {"Cook": 5}, 5, "u") is True
    assert record(h, "healthchoice", "August 2026", {"Cook": 5}, 5, "u") is False


def test_months_sorted_chronologically_not_alphabetically():
    h = load(None)
    for p in ["August 2026", "January 2026", "December 2025"]:
        record(h, "healthchoice", p, {"Cook": 1}, 1, "u")
    assert months(h, "healthchoice") == ["2025-12", "2026-01", "2026-08"]


def test_series_returns_county_over_time():
    h = load(None)
    record(h, "healthchoice", "January 2026", {"Cook": 10}, 10, "u")
    record(h, "healthchoice", "February 2026", {"Cook": 8}, 8, "u")
    assert series(h, "healthchoice", "Cook") == [("2026-01", 10), ("2026-02", 8)]


def test_to_key_rejects_garbage():
    for bad in ["", "Nonsense", "2026-08"]:
        try:
            to_key(bad)
            assert False, f"should have rejected {bad!r}"
        except ValueError:
            pass


def test_roundtrips_through_disk():
    h = load(None)
    record(h, "healthchoice", "August 2026", {"Cook": 5}, 5, "u")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "state.json")
        save(h, p)
        assert load(p)["programs"]["healthchoice"]["2026-08"]["total"] == 5


def test_problem_reported_once_then_suppressed():
    from tracker.history import note_problem, known_problems, clear_problem
    h = load(None)
    assert note_problem(h, "2022-03", "No county rows parsed", "2026-09-21") is True
    assert note_problem(h, "2022-03", "No county rows parsed", "2026-09-28") is False
    assert known_problems(h)["2022-03"]["first_seen"] == "2026-09-21"
    assert known_problems(h)["2022-03"]["last_seen"] == "2026-09-28"


def test_cleared_problem_reports_again_if_it_returns():
    from tracker.history import note_problem, clear_problem
    h = load(None)
    note_problem(h, "2022-03", "x", "2026-09-21")
    clear_problem(h, "2022-03")
    assert note_problem(h, "2022-03", "x", "2026-10-05") is True
