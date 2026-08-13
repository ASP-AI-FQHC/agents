# FQHC Prospect Intelligence

A prospect database of Federally Qualified Health Centers, built for
**Allstar Partners** from free, authoritative public data — HRSA's health center
downloads and the ProPublica Nonprofit Explorer API. It replaces a paid Cause IQ
Pro subscription with a local SQLite database and a branded web dashboard.

No API keys. No paid data sources. No scraping behind logins.

## Quick start

```bash
pip install -r requirements.txt
python -m pipeline.run        # build the database from public sources
uvicorn app.main:app          # then open http://127.0.0.1:8000
```

## How it works

| Stage | Module | What it does |
| --- | --- | --- |
| 1. Universe | `pipeline/hrsa.py` | Downloads HRSA's service delivery site and awardee files, deduplicates sites up to the grantee organization, counts sites per organization. |
| 2. EIN resolution | `pipeline/matching.py` | Fuzzy-matches each organization to an EIN via the ProPublica search endpoint, with a confidence score. |
| 3. Financials | `pipeline/propublica.py` | Pulls the three most recent Form 990 filings per EIN — revenue, expenses, assets, and the 990 PDF link. |
| 4. Scoring | `pipeline/scoring.py` | Produces a 0–100 composite ICP score with a per-factor breakdown. |
| 5. Dashboard | `app/` | FastAPI + Jinja2 UI: master table, organization detail, EIN review queue, CSV/XLSX export. |

## Configuration

Every threshold lives in [`config.yaml`](config.yaml) — revenue band, minimum
site count, target states, scoring weights, match thresholds, cache TTL and API
politeness settings. Nothing is hard-coded in the modules. Edit the file and
re-run `python -m pipeline.run` to apply the change.

## Data integrity rules

These are enforced in code, not just documented:

- **Nothing is fabricated.** Unavailable figures are stored as `NULL` and
  rendered as "Not available". No interpolation, no estimates, no defaults.
- **Ambiguous EIN matches are never silently accepted.** Scores at or above 90
  are auto-accepted; 70–89 go to a human review queue in the UI; below 70 is
  left unmatched and flagged.
- **Uncertainty is displayed, not hidden.** Match confidence and 990 filing age
  are shown on every row that has them.
- **Degrades gracefully.** If HRSA or ProPublica is unreachable, the app runs on
  cached data and labels it with the cache date.

## Data sources

- HRSA Data Downloads — <https://data.hrsa.gov/data/download>
  ("Health Center Service Delivery and Look-Alike Sites" and the Health Center
  Program awardee file). Downloads are cached in `data/raw/` for 30 days.
- ProPublica Nonprofit Explorer API v2 —
  <https://projects.propublica.org/nonprofits/api/> (free, unauthenticated;
  requests are throttled to 1/second).

If HRSA renames a download file, update the URL in `config.yaml` — or download
the CSV by hand and drop it into `data/raw/` under the configured filename. The
pipeline uses whatever is in the cache when the network is unavailable.

## Development

```bash
python -m pytest          # scoring, EIN matching, dedup, cache TTL
```

Layout:

```
config.yaml          all tunable thresholds and endpoints
app/                 FastAPI app, ORM models, templates, brand CSS
pipeline/            ingestion, matching, enrichment, scoring, CLI
tests/               pytest suite + fixtures
data/                SQLite database and cached raw downloads (gitignored)
```

## Branding

The UI implements the Allstar Partners Brand Style Guide: the six brand colors
as CSS variables, Open Sans, the six-color star band at the top edge of every
page, left-edge color pipes on KPI cards and callouts, blue page titles,
uppercase green section headers, and the standard footer. See
`app/static/css/brand.css`.
