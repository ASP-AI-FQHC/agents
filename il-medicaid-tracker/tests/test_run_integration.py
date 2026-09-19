import os
import tempfile

from tracker.history import load, record, save, months
from tracker.deltas import rank_changes
from tracker.report import compose
from tracker.watch import fingerprint, diff_fingerprints


def test_full_pipeline_history_to_report():
    h = load(None)
    record(h, "healthchoice", "July 2026", {"Cook": 1000, "Adams": 500}, 1500, "u1")
    record(h, "healthchoice", "August 2026", {"Cook": 900, "Adams": 500}, 1400, "u2")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "state.json")
        save(h, p)
        reloaded = load(p)
    assert months(reloaded, "healthchoice") == ["2026-07", "2026-08"]
    ranked = rank_changes(reloaded, "healthchoice", "2026-08")
    body = compose("2026-08", 1400, -100, ranked, [], [])
    assert "Cook" in body and "-100" in body
    assert "managed care" in body.lower()


def test_first_run_watch_baseline_is_silent_then_reports():
    """A first run must not mail every link on every watched page."""
    page_v1 = '<a href="/a.pdf">Report A</a>'
    page_v2 = page_v1 + '<a href="/b.pdf">Eligibility Redetermination Report - January 2027</a>'
    baseline = fingerprint(page_v1)
    assert diff_fingerprints(baseline, fingerprint(page_v1))["added"] == {}
    added = diff_fingerprints(baseline, fingerprint(page_v2))["added"]
    assert any("January 2027" in k for k in added)
