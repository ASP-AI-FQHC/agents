import pytest
from tracker.extract import extract_text, ExtractorUnavailable, _which_extractor


def test_reports_which_extractor_is_available():
    name = _which_extractor()
    assert name in ("pdftotext", "pdfplumber", None)


def test_raises_clearly_when_no_extractor(monkeypatch):
    monkeypatch.setattr("tracker.extract._which_extractor", lambda: None)
    with pytest.raises(ExtractorUnavailable) as e:
        extract_text("anything.pdf")
    assert "poppler-utils" in str(e.value)
