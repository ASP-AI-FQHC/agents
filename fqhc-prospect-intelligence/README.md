# FQHC Prospect Intelligence

A prospect database of Federally Qualified Health Centers, built for
**Allstar Partners** from free, authoritative public data — HRSA's health center
downloads and the ProPublica Nonprofit Explorer API. It replaces a paid Cause IQ
Pro subscription with a local SQLite database and a branded web dashboard.

No API keys. No paid data sources. No scraping behind logins.

## Install it (macOS)

Double-click **`Install FQHC Prospect Intelligence.command`** in Finder.

It builds the Python environment, puts a real app in `~/Applications` you can
drag to the Dock, and offers to schedule a daily data refresh. Everything it
does is printed as it happens, and if setup fails it stops rather than leaving
an app that cannot start.

The app it installs runs the code in this folder rather than a frozen copy, so
`git pull` updates it with no reinstall.

### Updating and rebuilding

Double-click **`Update and Refresh.command`**. It runs from its own folder, so
there is no directory to be in and no environment to activate — the two things
that go wrong doing this by hand. It pulls the latest version, files any UDS or
IRS downloads sitting in `~/Downloads` (copying, never moving), rebuilds the
data, and leaves the whole log in `run.log`.

### Daily refresh

The application is a window somebody opens, not a server, so a daily pull
cannot live inside it — the app is shut most of the time. The installer sets up
a macOS LaunchAgent instead, which runs the pipeline once a day whether or not
the app is open, and catches up at the next login if the Mac was asleep.

```bash
python -m desktop.schedule install --hour 6   # or any hour, local time
python -m desktop.schedule status             # what is scheduled, and where its log is
python -m desktop.schedule remove
```

It runs at low priority, writes to `logs/daily-refresh.log` in the data
directory, and does not start a run at the moment you install it. On Linux use
cron or a systemd timer to run `python -m pipeline.run`; on Windows, Task
Scheduler.

Only the sources that move daily are worth a daily pull — HRSA republishes the
site file every day. The 990, UDS and IRS Part VII data are annual, and the
pipeline's own caching means a daily run re-reads them without re-fetching.

## From a terminal instead

```bash
./setup_macos.sh
source .venv/bin/activate
python -m desktop.main
```

If no new-enough Python is installed it says so and tells you how to get one,
rather than failing later.

**Requires Python 3.11 or newer.** macOS ships 3.9 with the Xcode Command Line
Tools, which is too old — check with `python3 --version` before creating the
virtualenv, and use `brew install python@3.12` (or the python.org installer) if
needed. The app refuses to start on an older interpreter with an explanation
rather than failing obscurely inside SQLAlchemy.

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
| 4. People | `pipeline/irs.py` | Reads Form 990 Part VII from IRS e-file XML: officers, board members and contractors paid over $100,000. |
| 5. UDS | `pipeline/uds.py` | Loads HRSA Uniform Data System exports: patients, visits, staffing FTEs and payer mix per organization per year. |
| 6. Grants | `pipeline/grants.py` | Loads federal award files you download, and — when switched on — finds grants made to these organizations in every other nonprofit's Form 990 Schedule I. |
| 7. Websites | `pipeline/website.py` | Falls back to the organization's own leadership and board pages for the health centers with no filing on hand. |
| 8. Scoring | `pipeline/scoring.py` | Produces a 0–100 composite ICP score with a per-factor breakdown. |
| 9. Changes | `pipeline/changes.py` | Compares every organization to the previous run and logs what moved. |
| 10. Dashboard | `app/` | Master table, organization detail, EIN review queue, what-changed log, CSV/XLSX export, refresh. |

Run stages individually while iterating:

```bash
python -m pipeline.run --stage hrsa               # local and fast; run this first
python -m pipeline.run --stage ein --limit 20     # trial run: 20 organizations
python -m pipeline.run --stage ein --stage financials
python -m pipeline.run --stage scoring            # re-rank after editing weights
python -m pipeline.run --force-refresh            # ignore the 30-day download cache
```

The stages run in order and each depends on the ones before it. In particular
`--stage people` and `--stage website` read organizations that earlier stages
created, so running either on its own against an empty database finds nothing —
and says so, naming the stage to run first rather than reporting a bland zero.

`--limit N` caps the source-bound stages (`ein`, `financials`, `people`,
`website`) at N organizations,
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

### Where the database lives

Run from a checkout — `python -m pipeline.run`, `uvicorn app.main:app` and
`python -m desktop.main` alike — everything lives in `data/` next to the code.
Only the packaged `.app` uses a separate per-user directory
(`~/Library/Application Support/Allstar Partners/…`), because an application
bundle is read-only and cannot be written to.

Both the pipeline and the desktop launcher print the database file they are
using on startup, and the app's empty state names it too. An app pointed at a
different database than the pipeline just built looks exactly like a pipeline
that found nothing, so the path is always stated rather than assumed. Override
it for both with `FQHC_DATA_DIR`.

## How prospects are scored

Four factors, combined into a weighted 0–100 composite:

| Factor | Default weight | Full credit at |
| --- | --- | --- |
| Annual revenue | 35 | $5M–$50M, tapering to zero at $1M and $150M |
| Delivery sites | 25 | 10 sites (3 sites — the minimum — scores 60) |
| State footprint | 20 | IL, WI, IN, MI |
| Grant dependence | 20 | Federal award ≥ 50% of revenue |

Two factors have more than one possible source, and the order is deliberate:

- **Revenue** prefers the Form 990 — audited, and comparable across every
  nonprofit in the database. UDS fills in for the many health centers whose EIN
  is unresolved or whose filing has not been pulled.
- **Grant dependence** prefers UDS, which reports the grant and the total for the
  same organization, year and basis. A HRSA award over 990 revenue mixes two
  periods and two reporting bases, so it is the fallback.

Whichever was used is named in the factor's detail on the profile, so no score is
a number without provenance.

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

- **Patient volume** moved, year on year, from a newer UDS report — the earliest
  growth signal a health center publishes, well ahead of revenue
- **Delivery sites** opened or closed
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

## What is on an organization's profile

The profile is a reference document rather than a page to read top to bottom.
It has a standing contents list down the left, and twelve sections addressable
from it: Summary, Program areas, Funding, Financials, Service volume,
Personnel, Vendors, Regulatory status, Delivery sites, ICP score, Data update
history and Similar organizations. Each section states, underneath itself, the
filing or file it was built from.

The Summary opens with a strip of identifying facts (EIN, IRS classification,
employees, city, state, year formed, most recent filing, NTEE code), four
headline figures — revenues, expenses, assets and liabilities — each carrying
its movement against the previous *filed* year and, where the filing reports
them, the components underneath, then the organization's own description of
itself and a set of classification pills. Every pill names the filing it came
from on hover; none of them is a judgement made here.

| Data point | Source | Availability |
| --- | --- | --- |
| Headline revenues, expenses, assets and liabilities, with year-on-year movement | ProPublica Nonprofit Explorer, with the balance sheet completed from the IRS e-file XML for the same tax year | Available. Movement is shown only where the previous filed year reports the same figure — one filed year is not evidence of stability, and the card says so rather than leaving the space blank |
| Description of the organization | Form 990 Part I / Part III mission statement, reproduced verbatim | Available once Form 990 XML is loaded |
| Year formed, state of legal domicile, employees, volunteers | Form 990 Part I | Available once Form 990 XML is loaded; employee count also comes from ProPublica where the extract carries it |
| Program areas, in the filer's own words | Form 990 Part III program service accomplishments — description, expenses, grants made and revenue per program | Available once Form 990 XML is loaded |
| Functional expense split (program / management / fundraising) | Form 990 Part IX totals row | Available once Form 990 XML is loaded |
| Regulatory status: audited statements, Single Audit required and performed, audit committee | Form 990 Part XII. Three-state: yes, no, or "the return does not answer" | Available once Form 990 XML is loaded. The *name* of the Single Audit firm is published by the Federal Audit Clearinghouse, not on the 990, and that source is not loaded — so no auditor is named |
| Program areas as classified by funders | HRSA awardee file (all Section 330 funding streams) + IRS NTEE code | Available |
| Active and awarded federal grants | A federal award export you download (USAspending, or an agency's own), matched on an exact EIN | Available once an award file is loaded (see below). Only these rows can say whether an award is still running, because only they carry a period of performance; a row with no reported end date is shown as "not stated" rather than assumed to be active or ended |
| Grants received from other nonprofits | Other organizations' Form 990 Schedule I, where this organization is the named recipient | Available once Schedule I scanning is switched on. A nonprofit reports the grants it makes and never the ones it receives, so this is read out of everybody else's filing. History, never a current position, and only as complete as the Form 990 download on the machine |
| Financials and Form 990s | ProPublica Nonprofit Explorer, three most recent filings | Available |
| Funding sources | Form 990 revenue composition: contributions and grants, program service revenue, government grants, investment income | Available where the IRS extract reports it |
| Data update history | This database: first seen, last confirmed, and every detected change | Available |
| Similar organizations | Computed here from state, footprint, revenue and IRS classification | Available |
| Delivery sites | HRSA site file | Available |
| Patients, visits, staffing FTEs, payer mix | HRSA Uniform Data System, per year | Available once a UDS export is loaded (see below) |
| Estimated proposal quantities | Derived here from UDS staffing FTEs and the site count | Available where staffing is reported. Always labelled as derived |
| Key personnel and board members | One table merging all three sources: Form 990 Part VII Section A, the HRSA UDS project director, and the organization's own leadership pages | Available. Every row names the source or sources that produced it and the date that source describes. Where the same person appears in more than one, the row is merged and lists both — the filing supplies the role and compensation, the website or UDS return supplies contact details — and the more authoritative source wins any disagreement. Names and titles filed in capitals are re-cased for reading; a name that already carries lower-case letters is left exactly as its source wrote it |
| Vendors and service providers | Form 990 Part VII Section B — contractors paid over $100,000, with the service described | Available once Form 990 XML is loaded. Grouped by kind of service (health IT, billing, audit, clinical staffing, facilities, insurance, consulting) by reading the description text; the description as filed is shown beside the label on every row, and a description that matches nothing is labelled "other services" rather than guessed at |
| Salaries | Form 990 Part VII: reportable compensation from the organization, from related organizations, and other compensation | Available for everyone the filing lists. Board members usually report $0, which is a reported figure and is shown as $0 — distinct from "not available" |
| Board member contact details | Only where the organization publishes them itself | **Mostly not available.** A 990 lists officers care-of the organization's own address; personal emails and direct phone numbers are not published, and the `people` table has no contact columns, so there is nowhere to put invented ones. On a leadership page an address printed beside a person is captured verbatim, linked or as plain text. Shared inboxes (`info@`, `reception@`, or any address that lands on more than one person) are dropped rather than passed off as somebody's direct line, and nothing is ever constructed from a name and a domain. |
| Software and technology used | HRSA UDS health IT return | **Available.** The health center names its EHR vendor and product to HRSA. What sits around it — network, endpoints, identity, backup — is still not reported anywhere, and is the opening. Form 990 contractor rows remain a secondary proxy for IT and billing vendors. |

Where a data point cannot be sourced it is labelled "Not available" rather than
approximated, and the profile says which of the two reasons applies: the source
does not report it for this organization, or no free source publishes it at all.

### Loading Form 990 people and contractors

ProPublica's API does not expose Part VII, so this comes from the IRS's own
e-file XML. The IRS has moved these files between an S3 bucket and bulk ZIP
downloads more than once, so nothing is fetched automatically by default:

1. Download the years you want from the
   [IRS Form 990 series downloads](https://www.irs.gov/charities-non-profits/form-990-series-downloads).
2. Drop the ZIPs, as downloaded, into `data/raw/irs_xml/` (or wherever
   `irs.local_directory` points). **There is no need to unpack them.**
3. Run `python -m pipeline.run --stage people`.

Documents are indexed by the EIN *inside* each file, not by its name, because
the IRS names bulk files by object id — `202441123456789012_public.xml` contains
no EIN at all. Loose `.xml` files work too, whatever they are called. A corrupt
archive costs only its own contents.

Both schema generations parse: a 2011 return and a 2023 one use different
element names for the same facts, and elements are matched by local name rather
than by exact path.

There is no automatic download. The IRS retired the per-document S3 bucket when
it moved to bulk ZIPs — the bucket is still reachable but empty — so
`irs.xml_url_template` is blank by default rather than pointing at something
that would silently return nothing. Set it, plus `irs.index_urls` and
`irs.fetch_remote: true`, if you have a working source.

The first run indexes every document in the folder, reading only the header of
each rather than decompressing it, and saves the finished index next to the
archives — so a multi-gigabyte download is scanned once and every later run
starts instantly.

**If the stage reports no people,** its output says which of the four possible
reasons applies rather than leaving you to guess. It prints the absolute path it
searched, how many `.xml` files and archives it found there, how many documents
it indexed and how many distinct EINs those cover, and then one of:

| What it says | What to do |
| --- | --- |
| `No organization has a confirmed EIN yet` | Run `python -m pipeline.run` — the `hrsa` and `ein` stages populate what this stage reads. |
| `No IRS XML directory at …` | Create that folder and put the download in it. |
| `… contains no .xml or .zip files` | The download is still in `~/Downloads`; copy it across. |
| `… cover N other EINs` | The archive is real but for filing years that do not include your organizations; download another year. |
| `… documents could not be read (Deflate64 …)` | Handled automatically: the archive is unpacked once with `ditto` or `bsdtar` into `<archive>.expanded/` beside it and indexed from there. You only see this line if neither tool is present — then `pip install zipfile-deflate64`, or expand the archive in Finder. |

Note that a packaged desktop build keeps its data in
`~/Library/Application Support/Allstar Partners/FQHC Prospect Intelligence/`,
not in the checkout — the path the stage prints is always the one it actually
used.

### Falling back to the organization's website

Only a fraction of health centers will have a filing in whichever IRS archive
you have downloaded, and even a filing that is present describes a tax year that
closed 12–24 months ago. So for any organization left with no Part VII people,
the `website` stage reads the leadership, board and "our team" pages on the
health center's own site, found from the web address in the HRSA data:

```bash
python -m pipeline.run --stage website
```

This is deliberately treated as weaker evidence than a filing:

- Results are stored in a **separate table** (`website_people`) and shown in a
  separate, labelled block on the profile. A filing and a heuristic never blend
  into one list.
- Every row carries **the page it came from**, linked, so a human can confirm it
  in one click.
- A row is only recorded when a plausible person's name sits next to a phrase
  that names a role. A capitalised phrase on its own is not evidence — "Patient
  Portal" reads like a name — so pages with no role labels yield nothing rather
  than noise.
- Emails appear **only** where the organization published a `mailto:` link
  itself. Nothing is ever constructed from a name and a domain.
- `robots.txt` is honoured, requests are throttled to `website.requests_per_second`,
  links off the organization's own host are not followed, and nothing behind a
  login is touched.
- Every attempt is recorded whether or not it found anything, so "not checked
  yet" stays distinguishable from "checked, and the site says nothing".

Set `website.only_when_missing: false` to collect current names even where a
filing exists — useful precisely because the website is usually more current
about who holds a post right now. Set `website.enabled: false` to switch the
stage off entirely.

What this can and cannot deliver, honestly: leadership pages reliably give the
executive team by name and title; board rosters are published by many health
centers but not all; individual staff email addresses are usually absent, and
where present are often images or obfuscated, in which case nothing is
captured.

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
- **Sources are never blended anonymously.** People read from a Form 990 and
  people read from a web page live in different tables in the database, and
  always will. On the profile they are shown in one list — a reader wants the
  people, not a tour of the filing system — but every row names the source or
  sources behind it, and a row assembled from two sources says so and shows
  both. Where they disagree the more authoritative source wins, and the weaker
  one only ever fills a gap: a web page never overwrites a signed filing.
  Weaker evidence is presented as weaker evidence rather than quietly promoted.
- **A label is not a fact.** Where the app groups or classifies something it
  read — the kind of service a contractor provides, a classification pill — the
  underlying text is shown beside the label, and the label says it was applied
  here. Nothing derived is allowed to look like something reported.
- **Arithmetic on two reported figures is shown; a residual is not.** Net
  assets, surplus and a program's net cost appear only when both of their
  inputs were actually filed. No component is invented to make a breakdown add
  up to its total, because on screen a residual is indistinguishable from a
  reported figure.
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
- IRS Form 990 series downloads —
  <https://www.irs.gov/charities-non-profits/form-990-series-downloads>
  (downloaded by hand, read locally, never fetched during a run by default).
- USAspending — <https://www.usaspending.gov/> (assistance award exports,
  downloaded by hand and read locally; Assistance Listing 93.224 is the Health
  Center Program).
- The organizations' own public websites, for leadership and board pages only —
  fetched politely, `robots.txt` honoured, nothing behind a login.

**If HRSA moves a download** — which it does — the pipeline finds it. When the
configured URL 404s it reads HRSA's own
[download index](https://data.hrsa.gov/data/download) and looks for a CSV whose
link names the file, then reports which URL actually worked so you can update
`config.yaml`. Renames are the normal case, not the exception, so asking the
publisher beats shipping a list of guesses.

Failing that: add the new address to `hrsa.sites_url_fallbacks` or
`hrsa.awardees_url_fallbacks`, which are tried before the index is searched, or
download the CSV by hand into `data/raw/` under the configured filename. The
pipeline uses whatever is cached when it cannot reach the network, and the error
message names the exact path to save to. Column names
are resolved by alias and keyword rather than exact position, so a renamed
column degrades to an empty field instead of breaking the run.

## The dashboard

- **Contacts export** — CSV or XLSX of every named person at the organizations
  the current filters select: one row each, with organization, ICP score, name,
  title, published email where there is one, and the source. Form 990 rows and
  website rows are both there and each says which it is, with the tax year or
  the page link, so a list that gets forwarded still carries the difference.
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
python -m pytest
```

`tests/test_end_to_end.py` is the acceptance test: it runs all five stages from
an empty database against fixture data with the API mocked, simulates a second
run a month later, and then boots the web app on the result to check the pages,
a review decision and both exports. The other modules test their units; this one
catches wiring mistakes between them.

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
./desktop/build_macos.sh
```

That produces two things in `dist/`:

- **`FQHC Prospect Intelligence.app`** — drag it to Applications and run it
- **`FQHC Prospect Intelligence.dmg`** — the single file to hand to someone
  else, laid out for drag-to-install (set `FQHC_SKIP_DMG=1` to skip it)

Run it from a checkout without packaging:

```bash
python -m desktop.main            # native window
python -m desktop.main --no-window  # serve only, open the URL yourself
```

**The build must run on macOS.** PyInstaller does not cross-compile, so a Mac
bundle can only be produced on a Mac.

**It also builds for one CPU architecture** — whichever your Python is. On an
Apple Silicon Mac that means an arm64-only app, which will not launch on a
colleague's Intel Mac. The build script reports which you are producing. To
build for both, use a universal2 Python and:

```bash
FQHC_TARGET_ARCH=universal2 ./desktop/build_macos.sh
```

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

The structure underneath the brand follows the prospect page in the
`healthcare-market-intelligence` skill, restated in Allstar's colors: a faintly
warm paper ground rather than flat white, so a card separates from the page
without needing a heavier border; a three-step ink scale, because most of what
is on these pages is a label, a value or a caveat and those are three different
weights of voice; hairline rules drawn as a 1px grid gap, which stays one
hairline however the cells wrap; sticky column headers on the tables long enough
to lose them; tabular figures on every number meant to be compared with the one
above it; and the brand's color pipe repeated at badge scale as an inset rule.
None of the brand's required elements moved.

### Light, dark and auto

The masthead carries a three-position theme control. **Auto** follows the
operating system and is the default — a Mac that switches at dusk takes the app
with it. **Light** and **Dark** override that in either direction and persist in
the browser.

Three details are load-bearing, and each has a test:

- The saved choice is applied by a small inline script in `<head>`, before the
  stylesheet loads. Applied any later, a dark preference shows a white page
  first and then repaints.
- The dark block is written twice — once under `prefers-color-scheme` guarded by
  `:not([data-theme="light"])`, once under `[data-theme="dark"]` — so an
  explicit choice wins in *both* directions. Guarded only one way, the toggle
  appears to work going dark and does nothing going light.
- Printing forces a white ground and black ink whatever the screen was showing,
  and unpins the sticky headers. A profile printed for a meeting out of the dark
  theme would otherwise arrive as light text on a dark page.

The control is hidden entirely when JavaScript is off, and the page still
follows the system setting: a switch that cannot switch anything is worse than
no switch. The brand colors themselves do not change between themes — the star
band is the same six colors either way — but each accent gets a lighter sibling
of the same hue for text, because #0094bb on a near-black ground is unreadable
at label size.

## Loading UDS

The Uniform Data System is the annual return every Section 330 grantee files
with HRSA. It carries the two facts a Form 990 never will, and the two that
matter most for sizing an engagement:

- **Patients and visits** — how big the organization is in the unit its own
  leadership thinks in.
- **Staffing FTEs** — what actually drives users, workstations and devices.
  Revenue is a distant proxy for headcount; UDS reports headcount directly.

Plus payer mix (Medicaid, Medicare, uninsured shares).

**Not sure you downloaded the right file?** Point the inspector at it before
running anything:

```bash
python -m pipeline.uds ~/Downloads/whatever_you_downloaded.csv
```

It says whether the file is usable UDS data, which columns it will read, which
it will leave blank, the reporting year, and the first few organizations by
name — or, if it is the wrong file, every column it found instead.

For a workbook it also lists the sheets and marks the one it read. HRSA
workbooks open on a cover sheet (`DataDumpType`, `ReportingYear`, a refresh
date) and keep the health centers behind it, so the reader searches every sheet
for the one that carries the data rather than trusting whichever was saved
last.

There is no stable download URL — HRSA moves it between years — so nothing is
fetched automatically:

1. Get the health-center-level export from
   [HRSA data reporting](https://data.hrsa.gov/tools/data-reporting).
2. Drop the CSV or XLSX into `data/raw/uds/`, one file per year. Name it with
   the year (`2023_UDS.csv`) if the file has no year column — several years'
   exports don't.
3. Run `python -m pipeline.run --stage uds`.

### Two shapes of UDS export

**A flat export** — one row per health center, readable column names — is read
directly.

**A universal report** is 23 sheets, one per UDS table, and needs assembling:

| Sheet | Carries |
| --- | --- |
| `HealthCenterInfo` | identity, address, and the **project director** with a direct phone and email |
| `Table3A` | patients |
| `Table4` | payer mix |
| `Table5` | staffing FTEs |
| `Table8A`, `Table9D`, `Table9E` | costs, revenue, grants |
| `HITInformation` | **the EHR the health center runs**, by vendor and product |

**The workbook explains itself.** Every sheet has two header rows: the form
codes (`T3a_L39_Ca` — Table 3A, line 39, column a) and, beneath them, what each
column actually holds ("Total Patients-Male Patients Column A", "Does your
health center currently have an electronic health record system installed and
in use?"). The reader merges the two, so a coded column resolves through the
same alias and keyword machinery as a plainly named one and nothing needs a
hand-built map of line numbers.

That second row has to be recognised, not read: taken as data it becomes a
health center whose name is a question. It is identified by its own shape —
several long prose labels, with the identifier columns dashed out — and a sheet
without one keeps all its rows.

Totals are summed across every column carrying the label, because UDS splits
them: "Total Patients" is two columns, male and female, and taking one halves
the count. Where a workbook has no description row at all, a fallback derives
the total arithmetically — the line equal to half the sum of every line, since
including a total in the sum is what doubles it — rather than trusting a line
number that moves between report years.

### The project director

`HealthCenterInfo` names the project director with a direct phone and email,
reported by the health center to its own funder. It is the one authoritative
named contact in the whole application — a Form 990 lists officers care of the
organization's address, and a website may or may not publish anyone. It appears
on the profile and in the contacts export, sourced as *HRSA UDS*.

Columns are resolved by alias and keyword, not by position, because UDS renames
its headers between years: "Total Patients" has also shipped as "Patients
Served" and "Total Number of Patients". A column that cannot be found becomes a
null, not a zero. A file whose layout is unrecognisable is reported and skipped
rather than guessed at.

Rows are attached to organizations by HRSA ID first, then grant number, and only
then by name — and a name match requires the state to agree and the name to be
unique within it. Two centers a name cannot tell apart are matched to neither:
attaching one organization's patient count to another is worse than a gap.

### Estimated proposal quantities

Where staffing is reported, the profile shows suggested quantities for a
proposal — sites, users, workstations, devices — derived from staff FTEs at the
ratios in `uds.devices_per_fte` and `uds.workstations_per_fte`.

This is the one derived figure in the application, and it is labelled as derived
everywhere it appears. It exists because the proposal builder prices per Site,
User, Workstation and Device, and staff headcount is the only free public signal
that predicts those numbers. Where staffing is not reported, no estimate is
shown at all — revenue and patient counts predict device counts far too weakly,
and a number nobody can defend is worse on a proposal than no number.

Nothing in UDS is patient-level. Every figure is an organization total.

## Loading grants

Two different questions, and only one source can answer each.

### Active and awarded federal grants

Download an assistance award export from
[usaspending.gov](https://www.usaspending.gov/) — filter by recipient state, and
by Assistance Listing **93.224** for the Health Center Program — and drop the CSV
in `data/raw/grants`. Then:

```bash
python -m pipeline.run --stage grants
```

Columns are resolved by name rather than by position, so an agency's own export
in a different shape loads too: `recipient_ein`, `award_id_fain`,
`total_obligated_amount`, `period_of_performance_current_end_date` and their
common variants are all understood, and a column that cannot be found becomes a
null rather than failing the file.

**A file with no recipient EIN column is refused**, and the stage says so. There
is no safe way to attach a federal award to an organization by name, and a grant
credited to the wrong health center is worse than no grant at all.

This is the only source that supports the word *active*: it carries a period of
performance. A row whose end date the file does not report is shown as **not
stated** — neither active nor ended, because neither is known.

### Grants received from other nonprofits

A nonprofit reports every grant it **makes**, on Form 990 Schedule I, and none
of the grants it **receives**. There is no line anywhere on a health center's
own return listing its funders. So the only way to see who has funded one is to
read every other return in the IRS download and look for its EIN:

```yaml
grants:
  scan_schedule_i: true
```

```bash
python -m pipeline.run --stage grants
```

This opens every document in `irs.local_directory`, which is why it is off by
default. Most returns carry no Schedule I at all, and those are rejected by a
substring test on the raw bytes before any XML is parsed; each archive is opened
once rather than once per document. It is still the slowest thing in the
pipeline, and it prints progress as it goes.

What comes back is what Cause IQ shows as "received grants": a named grantor,
their EIN, the purpose as they described it, and the amount — split into cash
and non-cash, because non-cash assistance is not money and adding the two
silently would overstate the cash.

Every row is matched on an **exact nine-digit EIN**. Never on a name.

Two honest limits. This is **history, not a current position**: a Schedule I row
carries the grantor's tax year and no period of performance, so nothing here is
ever labelled active. And the list is **only as complete as the Form 990
download on your machine** — a funder whose return you do not have is a funder
you will not see, which is a gap in coverage rather than evidence the grant does
not exist. The profile says both.
