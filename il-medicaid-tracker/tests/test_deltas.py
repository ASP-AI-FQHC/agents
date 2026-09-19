import pytest

from tracker.history import load, record
from tracker.deltas import rank_changes, baseline_change


def fixture():
    h = load(None)
    record(h, "healthchoice", "July 2026", {"Cook": 1000, "Adams": 500, "Will": 200}, 1700, "u")
    record(h, "healthchoice", "August 2026", {"Cook": 900, "Adams": 520, "Will": 200}, 1620, "u")
    return h


def test_ranks_biggest_decline_first():
    r = rank_changes(fixture(), "healthchoice", "2026-08")
    assert r[0]["county"] == "Cook"
    assert r[0]["change"] == -100
    assert round(r[0]["pct"], 1) == -10.0
    assert r[-1]["county"] == "Adams"     # the only gainer sorts last


def test_unchanged_county_has_zero_change():
    r = {d["county"]: d for d in rank_changes(fixture(), "healthchoice", "2026-08")}
    assert r["Will"]["change"] == 0


def test_baseline_change_spans_arbitrary_months():
    b = baseline_change(fixture(), "healthchoice", "2026-08", "2026-07")
    assert b["change"] == -80


def test_first_month_has_no_prior_and_returns_empty():
    assert rank_changes(fixture(), "healthchoice", "2026-07") == []


def test_unknown_month_raises():
    with pytest.raises(KeyError):
        rank_changes(fixture(), "healthchoice", "2099-01")


def test_county_absent_from_prior_month_is_skipped_not_crashed():
    h = fixture()
    h["programs"]["healthchoice"]["2026-08"]["counties"]["NewCounty"] = 7
    names = [r["county"] for r in rank_changes(h, "healthchoice", "2026-08")]
    assert "NewCounty" not in names
