"""Find enrollment PDFs on HFS index pages.

Filenames follow no stable pattern (082026aggenrreport.pdf, 072026aereport.pdf,
march2026aggenr.pdf), so links are discovered, never constructed, and selected
by their visible anchor text instead.

The text match is deliberately loose ("enrol"): HFS has shipped at least one
typo, "Enrolllment as of July 1, 2022". A stricter pattern silently drops that
month, which is exactly the kind of quiet gap this tracker must not have.
The month name is matched tolerantly for the same reason: HFS has also
shipped "Septermber 1, 2020".
"""
import difflib
import re

_ANCHOR = re.compile(r'<a\s[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_ENROL = re.compile(r"enrol", re.I)
_AS_OF = re.compile(r"as of\s+([A-Za-z]+)\s+\d{1,2},?\s*(\d{4})", re.I)
_YYYYMM = re.compile(r"(20\d{2})(0[1-9]|1[0-2])")
_MMYYYY = re.compile(r"^(0[1-9]|1[0-2])(20\d{2})")

_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def _text_of(raw):
    return " ".join(_TAGS.sub("", raw).replace("​", "").split())


def find_enrollment_links(html, base="https://hfs.illinois.gov"):
    """Return [(url, anchor_text)] for enrollment report PDFs, deduped."""
    seen, out = set(), []
    for href, raw in _ANCHOR.findall(html):
        text = _text_of(raw)
        if not _ENROL.search(text):
            continue
        url = href if href.startswith("http") else base + href
        if url in seen:
            continue
        seen.add(url)
        out.append((url, text))
    return out


def find_pdf_links(html, base="https://hfs.illinois.gov"):
    return [u for u, _ in find_enrollment_links(html, base)]


def period_from_text(text):
    """'Enrollment as of August 1, 2026' -> '202608'. Preferred over filenames."""
    m = _AS_OF.search(text or "")
    if not m:
        return None
    name = _month_index(m.group(1))
    if name is None:
        return None
    return f"{m.group(2)}{name + 1:02d}"


def _month_index(raw):
    """Month name to 0-based index, tolerating HFS misspellings."""
    name = raw.lower().strip()
    if name in _MONTHS:
        return _MONTHS.index(name)
    close = difflib.get_close_matches(name, _MONTHS, n=1, cutoff=0.8)
    return _MONTHS.index(close[0]) if close else None


def period_hint(url):
    name = url.rsplit("/", 1)[-1]
    m = _MMYYYY.match(name)
    if m:
        return m.group(2) + m.group(1)
    m = _YYYYMM.search(name)
    if m:
        return m.group(1) + m.group(2)
    return None
