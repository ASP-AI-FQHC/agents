from tracker.discover import (find_pdf_links, find_enrollment_links,
                              period_hint, period_from_text)

# Shaped like the real HFS index: enrollment links plus unrelated PDFs.
HTML = """
<a href="/a/082026aggenrreport.pdf" target="_blank">Enrollment as of August 1, 2026 (pdf)</a>
<a href="/a/072026aereport.pdf" target="_blank">Enrollment as of July 1, 2026 (pdf)</a>
<a href="/a/2207report.pdf" target="_blank">Enrolllment as of July 1, 2022 (pdf)</a>
<a href="/b/managedcaremap.pdf" title="Map">Managed Care Map as of January 1, 2026</a>
<a href="/b/mcomanual.pdf">Managed Care Manual for Medicaid Providers</a>
<a href="/a/082026aggenrreport.pdf">dupe</a>
"""


def test_selects_enrollment_links_by_anchor_text():
    links = find_pdf_links(HTML)
    assert len(links) == 3
    assert all(l.startswith("https://hfs.illinois.gov/") for l in links)
    assert not any("managedcaremap" in l or "mcomanual" in l for l in links)


def test_tolerates_hfs_spelling_typo():
    """HFS shipped 'Enrolllment as of July 1, 2022'; dropping it would be a silent gap."""
    texts = [t for _, t in find_enrollment_links(HTML)]
    assert any("Enrolllment" in t for t in texts)


def test_deduplicates_repeated_hrefs():
    assert len(find_pdf_links(HTML)) == len(set(find_pdf_links(HTML)))


def test_period_from_anchor_text():
    assert period_from_text("Enrollment as of August 1, 2026 (pdf)") == "202608"
    assert period_from_text("Enrolllment as of July 1, 2022 (pdf)") == "202207"
    assert period_from_text("Managed Care Manual") is None


def test_period_hint_reads_embedded_dates():
    assert period_hint("x/202504MCOAggregatedEnrollmentReport.pdf") == "202504"
    assert period_hint("x/082026aggenrreport.pdf") == "202608"
    assert period_hint("x/march2026aggenr.pdf") is None


def test_tolerates_misspelled_month_name():
    """HFS shipped 'Septermber 1, 2020'; a strict match loses that month entirely."""
    assert period_from_text("Enrollment as of Septermber 1, 2020 (pdf)") == "202009"


def test_does_not_confuse_similar_month_names():
    assert period_from_text("Enrollment as of June 1, 2026") == "202606"
    assert period_from_text("Enrollment as of July 1, 2026") == "202607"
    assert period_from_text("Enrollment as of Bananuary 1, 2026") is None
