from tracker.watch import fingerprint, diff_fingerprints

OLD = '<a href="/a/redet-may2024.pdf">Eligibility Redetermination Report - May 2024</a>'
NEW = OLD + '<a href="/a/redet-jan2027.pdf">Eligibility Redetermination Report - January 2027</a>'


def test_fingerprint_maps_text_to_href():
    fp = fingerprint(OLD)
    assert fp["Eligibility Redetermination Report - May 2024"] == "/a/redet-may2024.pdf"


def test_diff_detects_new_report():
    d = diff_fingerprints(fingerprint(OLD), fingerprint(NEW))
    assert any("January 2027" in t for t in d["added"])
    assert d["removed"] == {}


def test_noise_does_not_register_as_change():
    noisy = NEW + "<p>Page generated 12:04:11</p>"
    d = diff_fingerprints(fingerprint(NEW), fingerprint(noisy))
    assert d["added"] == {} and d["removed"] == {}


def test_nested_markup_in_link_text_is_flattened():
    fp = fingerprint('<a href="/x.pdf"><span>New </span><b>Report</b></a>')
    assert "New Report" in fp


def test_first_run_against_empty_baseline_reports_everything():
    d = diff_fingerprints({}, fingerprint(NEW))
    assert len(d["added"]) == 2
