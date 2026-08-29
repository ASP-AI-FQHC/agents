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
| 6. Websites | `pipeline/website.py` | Falls back to the organization's own leadership and board pages for the health centers with no filing on hand. |
| 7. Scoring | `pipeline/scoring.py` | Produces a 0–100 composite ICP score with a per-factor breakdown. |
| 8. Changes | `pipeline/changes.py` | Compares every organization to the previous run and logs what moved. |
| 9. Dashboard | `app/` | Master table, organization detail, EIN review queue, what-changed log, CSV/XLSX export, refresh. |

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

| Data point | Source | Availability |
| --- | --- | --- |
| Program areas | HRSA awardee file (all Section 330 funding streams) + IRS NTEE code | Available |
| Financials and Form 990s | ProPublica Nonprofit Explorer, three most recent filings | Available |
| Funding sources | Form 990 revenue composition: contributions and grants, program service revenue, government grants, investment income | Available where the IRS extract reports it |
| Data update history | This database: first seen, last confirmed, and every detected change | Available |
| Similar organizations | Computed here from state, footprint, revenue and IRS classification | Available |
| Delivery sites | HRSA site file | Available |
| Patients, visits, staffing FTEs, payer mix | HRSA Uniform Data System, per year | Available once a UDS export is loaded (see below) |
| Estimated proposal quantities | Derived here from UDS staffing FTEs and the site count | Available where staffing is reported. Always labelled as derived |
| Key personnel and board members | Form 990 Part VII Section A — names, titles, hours, compensation and role checkboxes | Available once Form 990 XML is loaded (see below) |
| Key personnel, fallback | The organization's own leadership / board / "our team" pages | Available for organizations HRSA publishes a web address for. Shown in a separate, labelled block with a link to the page each name came from |
| Vendors and service providers | Form 990 Part VII Section B — contractors paid over $100,000, with the service described | Available once Form 990 XML is loaded |
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
- **Sources are never blended.** People read from a Form 990 and people read
  from a web page live in different tables and appear in different, labelled
  blocks, each carrying where it came from. Weaker evidence is presented as
  weaker evidence rather than quietly promoted.
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
