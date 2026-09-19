"""PDF text extraction with a pluggable backend.

The cloud sandbox may lack poppler, so fall back to pdfplumber.
Layout preservation matters: the parser reads columns positionally.
"""
import shutil
import subprocess


class ExtractorUnavailable(RuntimeError):
    pass


def _which_extractor():
    if shutil.which("pdftotext"):
        return "pdftotext"
    try:
        import pdfplumber  # noqa: F401
        return "pdfplumber"
    except ImportError:
        return None


def extract_text(pdf_path):
    backend = _which_extractor()
    if backend is None:
        raise ExtractorUnavailable(
            "No PDF extractor. Install poppler-utils (provides pdftotext) "
            "or `pip install pdfplumber`."
        )
    if backend == "pdftotext":
        out = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((p.extract_text(layout=True) or "") for p in pdf.pages)
