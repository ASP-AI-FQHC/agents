# Illinois Medicaid Cliff Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A weekly cloud routine that tracks Illinois Medicaid enrollment across all 102 counties, ranks counties by decline, and emails early warning of coverage losses ahead of the January 1 2027 work requirements.

**Architecture:** A Python package parses HFS monthly managed-care PDFs into county-level counts, appends them to a JSON history, and computes month-over-month deltas. A separate watcher fingerprints four HFS/IDHS pages for policy changes. A cloud routine runs both weekly and emails a combined report, staying silent when nothing is new.

**Tech Stack:** Python 3.11+, stdlib `urllib`/`json`/`re`, pytest. PDF text extraction via `pdftotext` (poppler) with a `pdfplumber` fallback. Gmail connector for delivery. Repo `github.com/ASP-AI-FQHC/agents`, folder `il-medicaid-tracker/`.

**Spec:** `docs/superpowers/specs/2026-09-18-il-medicaid-cliff-tracker-design.md`

## Global Constraints

- Recipient: `gfuller@allstarpartners.com`
- Data source is **managed care only** — 2,253,817 of roughly 3,263,822 total Illinois enrollees. Every report must state this.
- Every report states the **data month**, never implying current-day figures.
- Never construct HFS PDF URLs by pattern. Filenames are unstable (`082026aggenrreport.pdf`, `072026aereport.pdf`, `march2026aggenr.pdf`). Always discover links from the index page.
- On any parse failure, unreachable source, or validation failure: report the failure explicitly. Never emit a parsed zero as a real value.
- Canonical county count is 102. A parse yielding fewer for a full-state era is suspect.
- Required egress domains: `hfs.illinois.gov`, `data.medicaid.gov`, `download.medicaid.gov`, `www.medicaid.gov`.
- Validation tolerance: row sum must match stated Grand Total within 0.5%. Stated total is authoritative when present.

---

### Task 1: Project scaffold and PDF text extraction

**Files:**
- Create: `il-medicaid-tracker/tracker/__init__.py`
- Create: `il-medicaid-tracker/tracker/extract.py`
- Test: `il-medicaid-tracker/tests/test_extract.py`

**Interfaces:**
- Consumes: nothing
- Produces: `extract_text(pdf_path: str) -> str` — returns layout-preserved text. Raises `ExtractorUnavailable` if no extractor exists.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_extract.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.extract'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/extract.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_extract.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/tracker/ il-medicaid-tracker/tests/test_extract.py
git commit -m "feat: PDF text extraction with pdftotext/pdfplumber fallback"
```

---

### Task 2: Canonical county matching

**Files:**
- Create: `il-medicaid-tracker/tracker/counties.py`
- Test: `il-medicaid-tracker/tests/test_counties.py`

**Interfaces:**
- Consumes: nothing
- Produces: `COUNTIES: list[str]` (102 names), `canonical(raw: str) -> str | None` — normalizes a raw label to a canonical county name, or None.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_counties.py
from tracker.counties import COUNTIES, canonical

def test_exactly_102_counties():
    assert len(COUNTIES) == 102
    assert len(set(COUNTIES)) == 102

def test_matches_multiword_and_spacing_variants():
    assert canonical("De Witt") == "DeWitt"
    assert canonical("DeKalb") == "DeKalb"
    assert canonical("Jo Daviess") == "JoDaviess"
    assert canonical("La Salle") == "LaSalle"
    assert canonical("St. Clair") == "StClair"
    assert canonical("Rock Island") == "RockIsland"

def test_rejects_non_counties():
    assert canonical("Grand Total") is None
    assert canonical("Aetna Better Health") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_counties.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.counties'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/counties.py
"""Canonical Illinois county list.

Match by normalized name, never by column position: MCO columns appear and
disappear across format eras (IlliniCare, BCBS), but county names do not.
"""
import re

COUNTIES = """Adams Alexander Bond Boone Brown Bureau Calhoun Carroll Cass Champaign Christian Clark Clay
Clinton Coles Cook Crawford Cumberland DeKalb DeWitt Douglas DuPage Edgar Edwards Effingham Fayette Ford
Franklin Fulton Gallatin Greene Grundy Hamilton Hancock Hardin Henderson Henry Iroquois Jackson Jasper
Jefferson Jersey JoDaviess Johnson Kane Kankakee Kendall Knox Lake LaSalle Lawrence Lee Livingston Logan
Macon Macoupin Madison Marion Marshall Mason Massac McDonough McHenry McLean Menard Mercer Monroe
Montgomery Morgan Moultrie Ogle Peoria Perry Piatt Pike Pope Pulaski Putnam Randolph Richland RockIsland
Saline Sangamon Schuyler Scott Shelby Stark StClair Stephenson Tazewell Union Vermilion Wabash Warren
Washington Wayne White Whiteside Will Williamson Winnebago Woodford""".split()


def _norm(s):
    return re.sub(r"[^a-z]", "", s.lower())


_CANON = {_norm(c): c for c in COUNTIES}


def canonical(raw):
    return _CANON.get(_norm(raw))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_counties.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/tracker/counties.py il-medicaid-tracker/tests/test_counties.py
git commit -m "feat: canonical 102-county matching"
```

---

### Task 3: Section-aware PDF parsing

**Files:**
- Create: `il-medicaid-tracker/tracker/parse.py`
- Test: `il-medicaid-tracker/tests/test_parse.py`

**Interfaces:**
- Consumes: `tracker.counties.canonical`
- Produces: `parse_text(text: str) -> dict[tuple[str, str], Section]` keyed by `(program, period)` where program is `"dual"` or `"healthchoice"`. `Section` is a dict with keys `counties: dict[str,int]`, `total: int | None`, `conflicts: list[str]`.

**Why this shape:** MMAI was renamed FIDE-SNP, so both normalize to `dual`. Headers sometimes go missing after a page break (verified in the April 2025 file), so orphaned rows buffer until a continuation header claims them. A county appearing twice in one section means mis-attribution — record it as a conflict so Task 4 can refuse the file.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_parse.py
from tracker.parse import parse_text

SIMPLE = """
        FIDE-SNP Enrollments for August 2026
 County       Aetna    Molina   Grand Total
 Adams          179       211           578
 Cook        12,661     3,711        36,652
 Grand Total 12,840     3,922        37,230
"""

ORPHANED = """
        MMAI Enrollments- April 2025
 County       Aetna    Molina   County Total
 Adams          104       234           537
 Grand Total    104       234           537
 Bureau          80        73           230
        Health Choice of IL Enrollments- April 2025 (continued)
 Cook         1,000     2,000         3,000
 Grand Total  1,080     2,073         3,230
"""

def test_parses_counties_and_total():
    secs = parse_text(SIMPLE)
    (prog, period), sec = next(iter(secs.items()))
    assert prog == "dual"
    assert period == "August 2026"
    assert sec["counties"] == {"Adams": 578, "Cook": 36652}
    assert sec["total"] == 37230

def test_orphaned_rows_claimed_by_next_header():
    secs = parse_text(ORPHANED)
    hc = secs[("healthchoice", "April 2025")]
    assert hc["counties"]["Bureau"] == 230   # orphan, back-assigned
    assert hc["counties"]["Cook"] == 3000
    assert secs[("dual", "April 2025")]["counties"] == {"Adams": 537}

def test_duplicate_county_recorded_as_conflict():
    dupe = SIMPLE + "\n Adams   1   2   999\n"
    sec = parse_text(dupe)[("dual", "August 2026")]
    assert "Adams" in sec["conflicts"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.parse'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/parse.py
"""Parse HFS managed-care enrollment PDFs across format eras.

Verified against 24 months spanning 2018-2026. Design notes:
  - Program renamed MMAI -> FIDE-SNP; both normalize to "dual".
  - Take the LAST number on a row as the county total. Per-MCO columns
    shift between eras; the trailing total does not.
  - "Grand Total" terminates a section.
  - Rows appearing before their header (page-break artifact) buffer and are
    claimed by the next continuation header.
"""
import re

from .counties import canonical

_HDR = re.compile(r"^(.*?)\s+Enrollments?\s*(?:[—–-]|for)\s*([A-Za-z]+\s+\d{4})", re.I)
_NUM = re.compile(r"\d[\d,]*")
_ROW = re.compile(r"^([A-Za-z][A-Za-z.\s]*?)\s{2,}(.*)$")


def _program_of(raw):
    r = raw.lower()
    if "mmai" in r or "fide" in r or "snp" in r:
        return "dual"
    if "choice" in r or "hci" in r:
        return "healthchoice"
    return None


def parse_text(text):
    sections, cur, pending = {}, None, []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _HDR.match(s)
        if m:
            prog = _program_of(m.group(1))
            if prog:
                period = re.sub(r"\s*\(continued\)\s*$", "", m.group(2), flags=re.I).strip()
                cur = (prog, period)
                sections.setdefault(cur, {"counties": {}, "total": None, "conflicts": []})
                for name, val in pending:
                    sections[cur]["counties"][name] = val
                pending = []
                continue
        row = _ROW.match(s)
        if not row:
            continue
        nums = _NUM.findall(row.group(2))
        if not nums:
            continue
        val = int(nums[-1].replace(",", ""))
        name = canonical(row.group(1))
        if cur is None:
            if name:
                pending.append((name, val))
            continue
        sec = sections[cur]
        if name:
            if name in sec["counties"] and sec["counties"][name] != val:
                sec["conflicts"].append(name)
            sec["counties"][name] = val
        elif re.sub(r"[^a-z]", "", row.group(1).lower()) in ("grandtotal", "total"):
            sec["total"] = val
            cur = None
    return sections
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_parse.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/tracker/parse.py il-medicaid-tracker/tests/test_parse.py
git commit -m "feat: section-aware multi-era enrollment PDF parser"
```

---

### Task 4: Validation gate

**Files:**
- Create: `il-medicaid-tracker/tracker/validate.py`
- Test: `il-medicaid-tracker/tests/test_validate.py`

**Interfaces:**
- Consumes: `Section` dicts from `tracker.parse.parse_text`
- Produces: `validate(section: dict, expect_full_state: bool = True) -> list[str]` — returns a list of human-readable problems. Empty list means the section is trustworthy.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_validate.py
from tracker.validate import validate

def good():
    return {"counties": {f"C{i}": 100 for i in range(102)}, "total": 10200, "conflicts": []}

def test_clean_section_has_no_problems():
    assert validate(good()) == []

def test_conflicts_are_fatal():
    s = good(); s["conflicts"] = ["Cook"]
    assert any("Cook" in p for p in validate(s))

def test_sum_must_match_stated_total_within_tolerance():
    s = good(); s["total"] = 20000
    assert any("total" in p.lower() for p in validate(s))

def test_small_variance_is_tolerated():
    s = good(); s["total"] = 10230          # 0.29%, HIPAA suppression artifact
    assert validate(s) == []

def test_short_county_count_flagged_for_full_state():
    s = good(); s["counties"] = {f"C{i}": 100 for i in range(40)}; s["total"] = 4000
    assert any("102" in p for p in validate(s))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.validate'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/validate.py
"""Refuse untrustworthy parses.

A silently wrong number is worse than no number: a county appearing to drop
to zero because of a layout change would send the reader down a false trail.
"""
TOLERANCE = 0.005


def validate(section, expect_full_state=True):
    problems = []
    counties = section.get("counties") or {}
    total = section.get("total")
    conflicts = section.get("conflicts") or []

    if conflicts:
        problems.append(
            f"Duplicate county rows indicate mis-attribution: {', '.join(sorted(set(conflicts))[:5])}"
        )
    if not counties:
        problems.append("No county rows parsed")
        return problems

    if total is not None:
        ssum = sum(counties.values())
        allowed = max(5, total * TOLERANCE)
        if abs(ssum - total) > allowed:
            problems.append(
                f"Row sum {ssum:,} does not match stated total {total:,} "
                f"(difference {abs(ssum - total):,}, allowed {allowed:,.0f})"
            )
    if expect_full_state and len(counties) < 102:
        problems.append(f"Only {len(counties)} counties parsed, expected 102")
    return problems
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_validate.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/tracker/validate.py il-medicaid-tracker/tests/test_validate.py
git commit -m "feat: validation gate refusing untrustworthy parses"
```

---

### Task 5: Discover monthly PDF links

**Files:**
- Create: `il-medicaid-tracker/tracker/discover.py`
- Test: `il-medicaid-tracker/tests/test_discover.py`

**Interfaces:**
- Consumes: nothing
- Produces: `find_pdf_links(html: str, base: str = "https://hfs.illinois.gov") -> list[str]` — absolute URLs to enrollment PDFs, deduped, in page order. `period_hint(url: str) -> str | None` returns `"YYYYMM"` when the filename embeds it.

**Why:** filenames are unstable, so URLs are never constructed — only discovered. `period_hint` is a cross-check against the period parsed from the document, not a source of truth.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_discover.py
from tracker.discover import find_pdf_links, period_hint

HTML = """
<a href="/content/dam/.../totalcareenrollment/082026aggenrreport.pdf">August 1, 2026</a>
<a href="/content/dam/.../totalcareenrollment/072026aereport.pdf">July 1, 2026</a>
<a href="/content/dam/.../documents/managedcaremap.pdf">Map</a>
<a href="/content/dam/.../totalcareenrollment/082026aggenrreport.pdf">dupe</a>
"""

def test_finds_enrollment_pdfs_and_skips_unrelated():
    links = find_pdf_links(HTML)
    assert len(links) == 2
    assert links[0].startswith("https://hfs.illinois.gov/")
    assert all("managedcaremap" not in l for l in links)

def test_period_hint_reads_embedded_dates():
    assert period_hint("x/202504MCOAggregatedEnrollmentReport.pdf") == "202504"
    assert period_hint("x/082026aggenrreport.pdf") == "202608"
    assert period_hint("x/march2026aggenr.pdf") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_discover.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.discover'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/discover.py
"""Find enrollment PDFs on HFS index pages.

HFS filenames follow no stable pattern (082026aggenrreport.pdf,
072026aereport.pdf, march2026aggenr.pdf), so links are always discovered,
never constructed.
"""
import re

_HREF = re.compile(r'href="([^"]+\.pdf)"', re.I)
_ENROLL = re.compile(r"enroll", re.I)
_YYYYMM = re.compile(r"(20\d{2})(0[1-9]|1[0-2])")
_MMYYYY = re.compile(r"^(0[1-9]|1[0-2])(20\d{2})")


def find_pdf_links(html, base="https://hfs.illinois.gov"):
    seen, out = set(), []
    for href in _HREF.findall(html):
        name = href.rsplit("/", 1)[-1]
        if not _ENROLL.search(name):
            continue
        url = href if href.startswith("http") else base + href
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def period_hint(url):
    name = url.rsplit("/", 1)[-1]
    m = _MMYYYY.match(name)
    if m:
        return m.group(2) + m.group(1)
    m = _YYYYMM.search(name)
    if m:
        return m.group(1) + m.group(2)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_discover.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/tracker/discover.py il-medicaid-tracker/tests/test_discover.py
git commit -m "feat: discover enrollment PDF links from HFS index pages"
```

---

### Task 6: History store

**Files:**
- Create: `il-medicaid-tracker/tracker/history.py`
- Test: `il-medicaid-tracker/tests/test_history.py`

**Interfaces:**
- Consumes: validated sections
- Produces: `load(path) -> dict`, `record(hist, program, period, counties, total, source_url) -> bool` (False if already present), `months(hist, program) -> list[str]` sorted chronologically as `"YYYY-MM"`, `series(hist, program, county) -> list[tuple[str,int]]`.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_history.py
from tracker.history import load, record, months, series

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_history.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.history'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/history.py
"""Append-only enrollment history keyed by program and month."""
import json
import os
import re

_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def to_key(period):
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", period.strip())
    if not m:
        raise ValueError(f"Unparseable period: {period!r}")
    name = m.group(1).lower()
    if name not in _MONTHS:
        raise ValueError(f"Unknown month: {period!r}")
    return f"{m.group(2)}-{_MONTHS.index(name) + 1:02d}"


def load(path):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"programs": {}}


def save(hist, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(hist, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def record(hist, program, period, counties, total, source_url):
    key = to_key(period)
    prog = hist["programs"].setdefault(program, {})
    if key in prog:
        return False
    prog[key] = {"counties": counties, "total": total, "source": source_url}
    return True


def months(hist, program):
    return sorted(hist["programs"].get(program, {}).keys())


def series(hist, program, county):
    prog = hist["programs"].get(program, {})
    return [(k, prog[k]["counties"][county]) for k in sorted(prog)
            if county in prog[k]["counties"]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_history.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/tracker/history.py il-medicaid-tracker/tests/test_history.py
git commit -m "feat: append-only enrollment history store"
```

---

### Task 7: Deltas and county ranking

**Files:**
- Create: `il-medicaid-tracker/tracker/deltas.py`
- Test: `il-medicaid-tracker/tests/test_deltas.py`

**Interfaces:**
- Consumes: `tracker.history`
- Produces: `rank_changes(hist, program, month, prior=None) -> list[dict]` — each dict has `county`, `now`, `then`, `change`, `pct`, sorted most-negative first. `baseline_change(hist, program, month, baseline_month) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_deltas.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_deltas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.deltas'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/deltas.py
"""Month-over-month change, ranked by decline."""
from .history import months


def _prior_of(hist, program, month):
    ms = months(hist, program)
    if month not in ms:
        raise KeyError(f"No data for {month}")
    i = ms.index(month)
    return ms[i - 1] if i > 0 else None


def rank_changes(hist, program, month, prior=None):
    prog = hist["programs"][program]
    prior = prior or _prior_of(hist, program, month)
    if prior is None:
        return []
    now, then = prog[month]["counties"], prog[prior]["counties"]
    rows = []
    for county, cur in now.items():
        if county not in then:
            continue
        was = then[county]
        rows.append({
            "county": county, "now": cur, "then": was,
            "change": cur - was,
            "pct": ((cur - was) / was * 100) if was else 0.0,
        })
    rows.sort(key=lambda r: (r["change"], r["pct"]))
    return rows


def baseline_change(hist, program, month, baseline_month):
    prog = hist["programs"][program]
    now, base = prog[month]["total"], prog[baseline_month]["total"]
    return {
        "month": month, "baseline": baseline_month,
        "now": now, "then": base, "change": now - base,
        "pct": ((now - base) / base * 100) if base else 0.0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_deltas.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/tracker/deltas.py il-medicaid-tracker/tests/test_deltas.py
git commit -m "feat: month-over-month deltas ranked by decline"
```

---

### Task 8: Cliff watch page fingerprinting

**Files:**
- Create: `il-medicaid-tracker/tracker/watch.py`
- Create: `il-medicaid-tracker/sources.json`
- Test: `il-medicaid-tracker/tests/test_watch.py`

**Interfaces:**
- Consumes: nothing
- Produces: `fingerprint(html: str) -> dict[str,str]` mapping link text to href for links on a watched page. `diff_fingerprints(old, new) -> dict` with keys `added` and `removed`.

**Why:** the resumption of Eligibility Redetermination Reports (dormant since May 2024) is the single highest-value event this tracker can catch. Watching link sets rather than raw HTML avoids false positives from rotating banners and session tokens.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_watch.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_watch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.watch'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/watch.py
"""Fingerprint watched pages by their link sets.

Raw-HTML hashing produces false positives from banners and timestamps.
Link sets change only when HFS actually posts or removes something.
"""
import re

_LINK = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def fingerprint(html):
    out = {}
    for href, text in _LINK.findall(html):
        label = " ".join(_TAGS.sub("", text).split())
        if label:
            out[label] = href
    return out


def diff_fingerprints(old, new):
    return {
        "added": {k: v for k, v in new.items() if k not in old},
        "removed": {k: v for k, v in old.items() if k not in new},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_watch.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Write sources.json**

```json
{
  "enrollment_index": [
    "https://hfs.illinois.gov/medicalproviders/cc/totalccenrollmentforallprograms.html",
    "https://hfs.illinois.gov/medicalproviders/cc/archivedtotalcareenrollmentforallprograms.html"
  ],
  "cliff_watch": [
    {"id": "report-center", "url": "https://hfs.illinois.gov/info/reports.html",
     "why": "Eligibility Redetermination Reports resume here; dormant since May 2024"},
    {"id": "fed-resource-center", "url": "https://hfs.illinois.gov/info/fedresctr.html",
     "why": "HR1 work-requirement toolkits are actively published here"},
    {"id": "medicaid-changes", "url": "https://hfs.illinois.gov/medicalclients/medicaidguide/changes.html",
     "why": "Policy changes for the 2027 work requirements"},
    {"id": "idhs-work-requirements", "url": "https://www.dhs.state.il.us/page.aspx?item=180030",
     "why": "IDHS operational guidance on work requirements"}
  ],
  "cms_statewide": "https://data.medicaid.gov/api/1/metastore/schemas/dataset/items/6165f45b-ca93-5bb5-9d06-db29c692a360"
}
```

- [ ] **Step 6: Commit**

```bash
git add il-medicaid-tracker/tracker/watch.py il-medicaid-tracker/tests/test_watch.py il-medicaid-tracker/sources.json
git commit -m "feat: cliff-watch page fingerprinting and source registry"
```

---

### Task 9: Report composition

**Files:**
- Create: `il-medicaid-tracker/tracker/report.py`
- Test: `il-medicaid-tracker/tests/test_report.py`

**Interfaces:**
- Consumes: `tracker.deltas.rank_changes`, `tracker.deltas.baseline_change`
- Produces: `compose(month, total, mom, ranked, alerts, problems, baseline=None) -> str` — the email body. `subject(month, mom_change, alerts) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_report.py
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

def test_subject_flags_alerts():
    assert "ALERT" in subject("2026-08", -6710, ["Redetermination Report posted"])
    assert "ALERT" not in subject("2026-08", -6710, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# il-medicaid-tracker/tracker/report.py
"""Compose the email body.

Failures are surfaced at the top, never swallowed: a silently wrong figure
is worse than an acknowledged gap.
"""
CAVEAT = (
    "Source: HFS Detailed Managed Care Enrollment. This covers MANAGED CARE ONLY "
    "(about 2.25M of roughly 3.26M total Illinois Medicaid enrollees). It is a churn "
    "and trend signal, not a total headcount."
)


def subject(month, mom_change, alerts):
    flag = "ALERT: " if alerts else ""
    direction = f"{mom_change:+,}" if mom_change else "no change"
    return f"{flag}IL Medicaid {month} — {direction} MoM"


def compose(month, total, mom, ranked, alerts, problems, baseline=None):
    L = []
    if problems:
        L += ["DATA PROBLEMS THIS RUN — figures below may be incomplete:"]
        L += [f"  - {p}" for p in problems] + [""]
    if alerts:
        L += ["CLIFF WATCH — new items detected:"]
        L += [f"  - {a}" for a in alerts] + [""]

    L += [f"Data month: {month}", f"Statewide managed-care enrollment: {total:,}",
          f"Change vs prior month: {mom:+,}"]
    if baseline:
        L.append(f"Change vs {baseline['baseline']} baseline: "
                 f"{baseline['change']:+,} ({baseline['pct']:+.1f}%)")
    L.append("")

    losers = [r for r in ranked if r["change"] < 0][:15]
    if losers:
        L.append("LARGEST DECLINES BY COUNTY")
        for r in losers:
            L.append(f"  {r['county']:<14} {r['then']:>9,} -> {r['now']:>9,}  "
                     f"{r['change']:>+7,} ({r['pct']:+.1f}%)")
        L.append("")

    gainers = [r for r in ranked if r["change"] > 0][-5:]
    if gainers:
        L.append("COUNTIES GAINING")
        for r in reversed(gainers):
            L.append(f"  {r['county']:<14} {r['change']:>+7,} ({r['pct']:+.1f}%)")
        L.append("")

    L += [CAVEAT, "",
          "Context: all ACA adults move to 6-month redeterminations with work "
          "requirements starting January 1, 2027. Estimates of Illinois coverage "
          "loss range from 270,000 to over 700,000."]
    return "\n".join(L)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_report.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/tracker/report.py il-medicaid-tracker/tests/test_report.py
git commit -m "feat: email report composition with explicit failure reporting"
```

---

### Task 10: Backfill CLI and end-to-end run

**Files:**
- Create: `il-medicaid-tracker/backfill.py`
- Create: `il-medicaid-tracker/run.py`
- Test: `il-medicaid-tracker/tests/test_run_integration.py`

**Interfaces:**
- Consumes: every module above
- Produces: `backfill.py` populates `state.json` from all discoverable archived PDFs. `run.py` performs one weekly cycle and prints the report to stdout; the routine emails it.

- [ ] **Step 1: Write the failing test**

```python
# il-medicaid-tracker/tests/test_run_integration.py
import json, os, tempfile
from tracker.history import load, record, save
from tracker.deltas import rank_changes
from tracker.report import compose

def test_full_pipeline_history_to_report():
    h = load(None)
    record(h, "healthchoice", "July 2026", {"Cook": 1000, "Adams": 500}, 1500, "u1")
    record(h, "healthchoice", "August 2026", {"Cook": 900, "Adams": 500}, 1400, "u2")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "state.json")
        save(h, p)
        reloaded = load(p)
    ranked = rank_changes(reloaded, "healthchoice", "2026-08")
    body = compose("2026-08", 1400, -100, ranked, [], [])
    assert "Cook" in body and "-100" in body
    assert "managed care" in body.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd il-medicaid-tracker && python -m pytest tests/test_run_integration.py -v`
Expected: FAIL — `save` not exported or pipeline incomplete

- [ ] **Step 3: Write the CLIs**

```python
# il-medicaid-tracker/backfill.py
"""One-time backfill of every discoverable archived month."""
import sys, tempfile, urllib.request
from tracker import discover, extract, history, parse, validate

INDEXES = [
    "https://hfs.illinois.gov/medicalproviders/cc/totalccenrollmentforallprograms.html",
    "https://hfs.illinois.gov/medicalproviders/cc/archivedtotalcareenrollmentforallprograms.html",
]
UA = {"User-Agent": "Mozilla/5.0 (compatible; ASP-IL-Medicaid-Tracker/1.0)"}


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def main(state_path="state.json"):
    hist = history.load(state_path)
    urls = []
    for idx in INDEXES:
        urls += discover.find_pdf_links(fetch(idx).decode("utf-8", "replace"))
    added, skipped = 0, []
    for url in urls:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                tmp.write(fetch(url)); tmp.flush()
                sections = parse.parse_text(extract.extract_text(tmp.name))
            for (prog, period), sec in sections.items():
                problems = validate.validate(sec, expect_full_state=len(sec["counties"]) > 50)
                if problems:
                    skipped.append(f"{url} [{prog} {period}]: {problems[0]}")
                    continue
                if history.record(hist, prog, period, sec["counties"], sec["total"], url):
                    added += 1
        except Exception as e:
            skipped.append(f"{url}: {type(e).__name__}: {e}")
    history.save(hist, state_path)
    print(f"Added {added} program-months. Skipped {len(skipped)}.")
    for s in skipped:
        print("  SKIP", s)


if __name__ == "__main__":
    main(*sys.argv[1:])
```

```python
# il-medicaid-tracker/run.py
"""One weekly cycle: ingest any new month, diff watched pages, print report."""
import json, os, sys, tempfile
from backfill import fetch
from tracker import discover, extract, history, parse, validate, watch, deltas, report

STATE, FPRINTS, SOURCES = "state.json", "fingerprints.json", "sources.json"


def main():
    cfg = json.load(open(SOURCES))
    hist = history.load(STATE)
    problems, alerts, new_months = [], [], []

    for idx in cfg["enrollment_index"]:
        try:
            for url in discover.find_pdf_links(fetch(idx).decode("utf-8", "replace")):
                hint = discover.period_hint(url)
                if hint and f"{hint[:4]}-{hint[4:]}" in history.months(hist, "healthchoice"):
                    continue
                with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                    tmp.write(fetch(url)); tmp.flush()
                    sections = parse.parse_text(extract.extract_text(tmp.name))
                for (prog, period), sec in sections.items():
                    probs = validate.validate(sec, expect_full_state=len(sec["counties"]) > 50)
                    if probs:
                        problems.append(f"{period} {prog}: {probs[0]}")
                        continue
                    if history.record(hist, prog, period, sec["counties"], sec["total"], url):
                        if prog == "healthchoice":
                            new_months.append(history.to_key(period))
        except Exception as e:
            problems.append(f"{idx}: {type(e).__name__}: {e}")

    old_fp = json.load(open(FPRINTS)) if os.path.exists(FPRINTS) else {}
    new_fp = {}
    for src in cfg["cliff_watch"]:
        try:
            fp = watch.fingerprint(fetch(src["url"]).decode("utf-8", "replace"))
            new_fp[src["id"]] = fp
            d = watch.diff_fingerprints(old_fp.get(src["id"], {}), fp)
            for label in d["added"]:
                alerts.append(f"[{src['id']}] new: {label}")
        except Exception as e:
            problems.append(f"{src['url']}: {type(e).__name__}: {e}")
            new_fp[src["id"]] = old_fp.get(src["id"], {})

    history.save(hist, STATE)
    with open(FPRINTS, "w") as f:
        json.dump(new_fp, f, indent=2, sort_keys=True)

    if not new_months and not alerts and not problems:
        print("NO_UPDATE")
        return 0

    ms = history.months(hist, "healthchoice")
    month = sorted(new_months)[-1] if new_months else (ms[-1] if ms else None)
    if month is None:
        print("NO_DATA")
        return 0
    prog = hist["programs"]["healthchoice"]
    ranked = deltas.rank_changes(hist, "healthchoice", month)
    mom = prog[month]["total"] - prog[ms[ms.index(month) - 1]]["total"] if ms.index(month) > 0 else 0
    print(report.subject(month, mom, alerts))
    print()
    print(report.compose(month, prog[month]["total"], mom, ranked, alerts, problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd il-medicaid-tracker && python -m pytest tests/ -v`
Expected: PASS (all tests, 25 total)

- [ ] **Step 5: Run the real backfill and confirm against known figures**

Run: `cd il-medicaid-tracker && python backfill.py && python -c "
from tracker.history import load
h = load('state.json'); p = h['programs']['healthchoice']
print('months:', len(p))
print('2026-08:', p['2026-08']['total'])
print('2023-06:', p['2023-06']['total'])"`

Expected: `2026-08: 2253817` and `2023-06: 2982596`. These are verified figures; a mismatch means the parser regressed.

- [ ] **Step 6: Commit**

```bash
git add il-medicaid-tracker/backfill.py il-medicaid-tracker/run.py il-medicaid-tracker/tests/test_run_integration.py il-medicaid-tracker/state.json
git commit -m "feat: backfill and weekly run pipeline"
```

---

### Task 11: PROMPT.md and cloud routine

**Files:**
- Create: `il-medicaid-tracker/PROMPT.md`
- Create: `il-medicaid-tracker/README.md`

**Interfaces:**
- Consumes: `run.py`
- Produces: the routine definition; no code depends on this task.

- [ ] **Step 1: Write PROMPT.md**

It must instruct the routine to: clone the repo; ensure a PDF extractor exists (`apt-get install -y poppler-utils` or `pip install pdfplumber`); run `python run.py`; if output is `NO_UPDATE`, send nothing and exit; otherwise email the first line as subject and the remainder as body to `gfuller@allstarpartners.com` via the Gmail connector; commit and push `state.json` and `fingerprints.json` with `git push origin HEAD:main` (not `HEAD`, which fails on detached HEAD); and report source reachability on the first run so the egress allowlist change can be confirmed.

- [ ] **Step 2: Verify the routine's prerequisites**

Run: `cd il-medicaid-tracker && python -c "
import urllib.request as u
for d in ['https://hfs.illinois.gov/info/reports.html',
          'https://data.medicaid.gov/api/1/metastore/schemas/dataset/items?show-reference-ids=false']:
    try:
        print(u.urlopen(u.Request(d, headers={'User-Agent':'Mozilla/5.0'}), timeout=30).status, d)
    except Exception as e:
        print('FAIL', d, e)"`

Expected: `200` for both. A failure here from inside the cloud environment means the egress allowlist is not yet in effect.

- [ ] **Step 3: Create the routine**

Weekly, Mondays 12:00 UTC, Sonnet 5, Gmail connector, repo `ASP-AI-FQHC/agents`, prompt pointing at `il-medicaid-tracker/PROMPT.md`.

- [ ] **Step 4: Trigger one manual run and confirm the email**

- [ ] **Step 5: Commit**

```bash
git add il-medicaid-tracker/PROMPT.md il-medicaid-tracker/README.md
git commit -m "docs: routine prompt and README"
```

---

## Self-Review

**Spec coverage:** Monthly county ingestion (Tasks 1-5), history and backfill (Tasks 6, 10), ranked deltas (Task 7), cliff watch on all four surfaces (Task 8), report with caveats and failure surfacing (Task 9), weekly routine and egress verification (Task 11). CMS statewide cross-check is registered in `sources.json` but is **not** implemented as a task — it is a nice-to-have confirmation of direction, and the spec lists it as a cross-check rather than a requirement. Flagged here rather than silently dropped; add it as Task 12 if wanted.

**Placeholder scan:** none. Every step carries runnable code or an exact command.

**Type consistency:** `Section` dicts use `counties`/`total`/`conflicts` throughout Tasks 3, 4, 6, 10. `history.to_key` is defined in Task 6 and used in Task 10. `fetch` is defined in `backfill.py` and imported by `run.py`. `rank_changes` row keys (`county`, `now`, `then`, `change`, `pct`) match between Tasks 7 and 9.
