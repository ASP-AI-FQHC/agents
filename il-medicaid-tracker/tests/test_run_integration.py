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


# --- run.py writes the HTML alternative next to the printed text -------------

def _seed(tmp_path):
    h = load(None)
    record(h, "healthchoice", "July 2026", {"Cook": 1000, "Adams": 500}, 1500, "u1")
    record(h, "healthchoice", "August 2026", {"Cook": 900, "Adams": 500}, 1400, "u2")
    save(h, str(tmp_path / "state.json"))
    (tmp_path / "sources.json").write_text('{"enrollment_index": [], "cliff_watch": []}')


def test_run_writes_html_matching_the_printed_report(tmp_path, monkeypatch, capsys):
    import run
    _seed(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run, "ingest_new_months", lambda *a: ["2026-08"])
    monkeypatch.setattr(run, "run_cliff_watch", lambda *a: ([], {}, False))
    assert run.main() == 0
    text = capsys.readouterr().out
    html = (tmp_path / "report.html").read_text()
    assert "Statewide managed-care enrollment: 1,400" in text
    assert "1,400" in html and "-100" in html and "Cook" in html


def test_quiet_run_leaves_no_stale_html(tmp_path, monkeypatch, capsys):
    """A NO_UPDATE week must not leave last week's HTML lying around to be sent."""
    import run
    _seed(tmp_path)
    (tmp_path / "report.html").write_text("<div>stale</div>")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run, "ingest_new_months", lambda *a: [])
    monkeypatch.setattr(run, "run_cliff_watch", lambda *a: ([], {}, False))
    assert run.main() == 0
    assert capsys.readouterr().out.strip() == "NO_UPDATE"
    assert not (tmp_path / "report.html").exists()
