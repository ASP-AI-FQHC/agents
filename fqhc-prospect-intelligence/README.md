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

The first run downloads the HRSA files, then queries ProPublica once per
second for each organization **in your scoring footprint** — the four states in
`config.yaml`, which is a few hundred organizations rather than the ~1,500
nationally. Expect roughly ten to fifteen minutes. Every response is cached, so
subsequent runs take a fraction of that, and the run is resumable: organizations
already resolved are skipped, so an interrupted run continues where it stopped.

The full national universe is still built locally by the HRSA stage — that is a
file parse, not an API call — so widening the footprint later is a config change
and a re-run of two stages, not a rebuild. Set
`pipeline.restrict_api_to_target_states: false` to resolve EINs for the whole
country, at roughly ten times the API traffic.

## What it does

| Stage | Module | What it does |
| --- | --- | --- |
| 1. Universe | `pipeline/hrsa.py` | Downloads HRSA's service delivery site and awardee files and deduplicates sites up to the grantee organization, so one row is one FQHC with a site count. |
| 2. EIN resolution | `pipeline/matching.py` | Fuzzy-matches each organization to an EIN via ProPublica search, with a confidence score that routes the match. |
| 3. Financials | `pipeline/propublica.py` | Pulls the three most recent Form 990 filings per EIN — revenue, expenses, assets, and the 990 PDF link. |
| 4. Scoring | `pipeline/scoring.py` | Produces a 0–100 composite ICP score with a per-factor breakdown. |
| 5. Changes | `pipeline/changes.py` | Compares every organization to the previous run and logs what moved. |
| 6. Dashboard | `app/` | Master table, organization detail, EIN review queue, what-changed log, CSV/XLSX export, refresh. |

Run stages individually while iterating:

```bash
python -m pipeline.run --stage hrsa               # local and fast; run this first
python -m pipeline.run --stage ein --limit 20     # trial run: 20 organizations
python -m pipeline.run --stage ein --stage financials
python -m pipeline.run --force-refresh            # ignore the 30-day download cache
```

`--limit N` caps the API-bound stages (`ein`, `financials`) at N organizations,
so you can confirm the live sources behave as expected in under a minute before
committing to a full pass. A capped run is announced at the start and again at
the end, so a partial pass is never mistaken for a complete one, and because the
pipeline skips organizations it has already settled, running again picks up
where the trial stopped.

## Configuration

Every threshold lives in [`config.yaml`](config.yaml) — revenue band, minimum
site count, target states, the four scoring weights, match thresholds, cache TTL
and API politeness settings. Nothing is hard-coded in the modules. Edit the file
and re-run `python -m pipeline.run --stage scoring` to re-rank without
re-fetching anything.

Point the app and the CLI at a different config with the `FQHC_CONFIG`
environment variable, or `python -m pipeline.run --config path/to/config.yaml`.

## How prospects are scored

Four factors, combined into a weighted 0–100 composite:

| Factor | Default weight | Full credit at |
| --- | --- | --- |
| Annual revenue | 35 | $5M–$50M, tapering to zero at $1M and $150M |
| Delivery sites | 25 | 10 sites (3 sites — the minimum — scores 60) |
| State footprint | 20 | IL, WI, IN, MI |
| Grant dependence | 20 | Federal award ≥ 50% of revenue |

**When a factor cannot be computed, it is dropped and the remaining weights are
renormalized** — an organization with no 990 on file is scored on what is
actually known about it. Scoring an unknown as zero would rank "we have no data"
alongside "genuinely poor fit", which is a different claim. The organization
detail page shows each factor's score, its effective weight after
renormalization, and the reason behind it.

## What changed

Re-running the pipeline monthly is where this earns its keep over a static
export. Each run compares every organization against the previous run and logs
real movement, browsable at `/changes` and filterable by kind:

- **Delivery sites** opened or closed — the clearest expansion signal there is
- **A newer 990** became available, with the revenue move against the prior year
- **Federal award** increased or decreased, as a percentage
- **Grantee type** changed — a look-alike becoming a Section 330 awardee
- Health centers **entering or leaving** the HRSA file

Two deliberate omissions. **Score movements are not logged**: the composite is
derived, so retuning a weight in `config.yaml` would fire an event for every
organization and bury the real signal. And **the first run is silent** — with no
baseline every organization would read as "new", so the first run records the
baseline and reports nothing.

An organization HRSA stops publishing is reported once and then kept. Its row
survives because human EIN decisions hang off it.

## Data integrity rules

These are enforced in code and covered by tests, not merely documented:

- **Nothing is fabricated.** Unavailable figures are stored as `NULL` and
  rendered as "Not available" — in the UI *and* in exports, where a blank cell
  would otherwise read as a zero. A reported zero and an unknown stay distinct.
- **Ambiguous EIN matches are never silently accepted.** Confidence ≥ 90
  auto-accepts; 70–89 goes to the review queue; below 70 stays unmatched with no
  EIN attached at all. Two candidates within a few points of each other both
  scoring above 90 is ambiguity, not confidence — that goes to review too.
- **Financials follow only confirmed EINs.** A pending or rejected match never
  carries revenue onto an organization, in the table, the detail page, the
  score, or an export.
- **Human decisions are durable.** An accepted match survives even a forced
  re-run; a rejected EIN is recorded and never proposed again, so the next run
  surfaces the next-best candidate instead.
- **Uncertainty is displayed.** Match confidence appears on every matched row,
  and each filing is badged with the age of the tax year it describes.
- **It degrades gracefully.** If HRSA or ProPublica is unreachable, the app runs
  on cached data and says so, with the cache date, on every page and inside
  every export.

## Data sources

- HRSA Data Downloads — <https://data.hrsa.gov/data/download> ("Health Center
  Service Delivery and Look-Alike Sites" plus the Health Center Program awardee
  file). Cached in `data/raw/` for 30 days.
- ProPublica Nonprofit Explorer API v2 —
  <https://projects.propublica.org/nonprofits/api/> (free, unauthenticated,
  throttled to one request per second with exponential backoff on 429/5xx).

**If HRSA moves a download:** update the URL in `config.yaml`, or download the
CSV by hand and drop it into `data/raw/` under the configured filename — the
pipeline uses whatever is cached when it cannot reach the network. Column names
are resolved by alias and keyword rather than exact position, so a renamed
column degrades to an empty field instead of breaking the run.

## The dashboard

- **Prospects** — sortable, filterable master table (score, state, revenue,
  sites, match status), a KPI summary strip and the top 10 prospects. Filtering
  updates in place; sorting, paging and filtering all work without JavaScript
  as ordinary links and form submissions.
- **Organization detail** — the score breakdown with per-factor reasoning,
  three years of 990 figures with freshness badges and PDF links, EIN with match
  provenance, and every delivery site.
- **Review queue** — pending EIN matches, least certain first, with the
  candidates ProPublica returned and confirm/reject buttons.
- **Export CSV / XLSX** — exactly the current filtered view, opening with a
  header block naming the company, the generation time, the filters applied and
  whether the data came from cache.
- **Refresh data** — re-runs the pipeline in the background with live progress.
  A stage that fails does not abandon the rest, and anything that completed on
  cached data is reported as such.

## Development

```bash
python -m pytest          # scoring, matching, ingestion, exports, routes
```

Layout:

```
config.yaml          all tunable thresholds and endpoints
app/                 FastAPI app, ORM models, queries, exports, templates, brand CSS
pipeline/            ingestion, matching, enrichment, scoring, CLI
tests/               pytest suite + HRSA and ProPublica fixtures
data/                SQLite database and cached raw downloads (gitignored)
```

The database is a rebuildable cache of public data. `init_db` adds newly
declared nullable columns to an existing database automatically; if a schema
change ever needs more than that, the app says so and asks you to delete
`data/fqhc.db` and re-run the pipeline.

## Desktop application (macOS)

The same app, in a native window, with no terminal and no Python install:

```bash
pip install -r requirements.txt -r requirements-desktop.txt
./desktop/build_macos.sh          # produces dist/"FQHC Prospect Intelligence.app"
```

Run it from a checkout without packaging:

```bash
python -m desktop.main            # native window
python -m desktop.main --no-window  # serve only, open the URL yourself
```

**The build must run on macOS.** PyInstaller does not cross-compile, so a Mac
bundle can only be produced on a Mac.

**Where a packaged build keeps its data.** The application bundle is read-only,
so the database, the download cache and an editable copy of `config.yaml` live
in `~/Library/Application Support/Allstar Partners/FQHC Prospect Intelligence`.
The config is seeded on first launch and never overwritten afterwards, so tuned
thresholds survive an upgrade. Delete that folder to reset the app completely.

**First launch shows an empty database** and a prompt to click *Refresh data*,
which runs the full pipeline in the background — usually ten to fifteen minutes
for a four-state footprint. The window can be closed and reopened while that
runs; completed work is kept.

**Gatekeeper.** The bundle is unsigned, so macOS refuses it on first open:
right-click the app, choose *Open*, and confirm — once per machine. Signing and
notarizing it properly needs an Apple Developer account; the exact commands are
printed at the end of the build script.

## Branding

The UI implements the Allstar Partners Brand Style Guide: the six brand colors
as CSS variables, Open Sans (300/400/700/800), the six-color star band flush to
the top edge of every page, left-edge color pipes on KPI cards and callouts
(blue for informational, green for qualified), blue page titles, uppercase green
section headers, and the standard two-line footer. See
`app/static/css/brand.css` — the palette is defined once at the top and
everything else refers to it.
