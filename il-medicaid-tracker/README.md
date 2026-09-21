# Illinois Medicaid Cliff Tracker

Tracks Illinois Medicaid managed-care enrollment across all 102 counties and
watches for coverage losses ahead of the January 1, 2027 work requirements.

## What the data is

Source is the HFS Detailed Managed Care Enrollment report, published monthly
with roughly a 30-day lag (August 1 data appeared September 2).

**This is managed care only** — about 2.25M of roughly 3.26M total Illinois
Medicaid enrollees. Fee-for-service is excluded. Treat it as a churn and trend
signal, not a headcount.

There is no real-time source. Monthly at ~30 days lag is the ceiling.

## Coverage

96 months ingested, January 2018 through August 2026. The 2023-2026 window,
which spans the unwinding and the current pre-cliff plateau, is complete.
Six months in 2021-2022 fail validation due to layout variants and are
reported as known problems rather than silently dropped.

Verified figures: 2023-06 = 2,982,596 (peak) · 2026-08 = 2,253,817.

## Usage

```bash
python backfill.py     # one-time history load, safe to re-run
python run.py          # one weekly cycle; prints NO_UPDATE when quiet
pytest                 # 61 tests
```

When a run has something to report, `run.py` prints the plain-text email and
also writes `report.html` (git-ignored), the same report as inline-styled
tables. The routine sends the text as `body` and the file as `htmlBody`.
Both come from `tracker/report.py`; change them together so they never diverge.

## Design notes

Three things in the source data will bite anyone modifying this:

- **Filenames are unstable** (`082026aggenrreport.pdf`, `072026aereport.pdf`,
  `march2026aggenr.pdf`). Links are discovered from index pages by their anchor
  text, never constructed.
- **HFS ships typos.** "Enrolllment as of July 1, 2022" and "Septermber 1,
  2020" both appear on live pages. Matching is deliberately tolerant; a strict
  pattern silently loses those months.
- **Older PDFs have broken ligatures.** 2018-2020 files render Christian as
  `Chris+an`, Fayette as `Faye e`, Piatt as `Pia`. `counties.canonical_fuzzy`
  recovers these by prefix-then-subsequence match, accepting only unambiguous
  hits. Unrecovered, this loses ~28,000 enrollees per month.

The validation gate refuses a month rather than recording a wrong number: row
sums must reconcile with the stated Grand Total within 0.5%, and a county
appearing twice in one section means rows from another program bled in.
