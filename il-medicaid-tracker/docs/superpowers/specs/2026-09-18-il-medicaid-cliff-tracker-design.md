# Illinois Medicaid Cliff Tracker — Design

**Date:** 2026-09-18
**Owner:** Guy Fuller (Allstar Partners)
**Recipient:** gfuller@allstarpartners.com

## Purpose

Track Illinois Medicaid enrollment by county and provide early warning for coverage
losses under the January 1, 2027 federal work requirements, which move all ACA adults
to six-month redetermination cycles. Published estimates of Illinois coverage loss
range from 270,000 to over 700,000 people.

The tracker must establish a clean pre-cliff baseline **before** January 2027. Built
afterward, it cannot distinguish cliff losses from normal churn.

## What is actually available

Verified 2026-09-18 by fetching and parsing the real sources.

| Source | Granularity | Cadence | Lag |
|---|---|---|---|
| HFS Detailed Managed Care Enrollment (PDF) | 102 counties x MCO | Monthly | ~30 days |
| CMS Performance Indicator dataset (CSV) | Illinois statewide | Monthly | ~3 months |
| HFS annual enrollment | zip, county, legislative district | Annual (FY end) | 90 days |
| HFS Eligibility Redetermination Reports | statewide | Dormant since May 2024 | n/a |

**There is no real-time source.** Monthly with ~30 days lag is the ceiling. Real-time
individual eligibility (MEDI / 270-271 EDI) is per-patient, credentialed, and
HIPAA-bound to treatment and payment operations; it cannot be aggregated into a
population tracker and is out of scope.

## Scope

**In scope**
- Monthly county-level enrollment from the HFS managed care PDF, all 102 counties
- Month-over-month change per county, ranked by decline
- Backfill of ~92 historical months (2018-2026) including the 2023-24 unwinding
- Weekly watch on four surfaces for cliff signals
- Email report to gfuller@allstarpartners.com, silent when nothing new

**Out of scope**
- Real-time or daily counts (do not exist)
- Zip-level change tracking (annual snapshots only)
- Individual beneficiary data (prohibited)
- Total Medicaid headcount (this source is managed care only)

## Data coverage

Managed care only: 2,253,817 of roughly 3,263,822 total Illinois Medicaid enrollees
(FY2025). Fee-for-service is excluded. This is a **churn and trend signal, not a
headcount**, and every email must say so.

Verified trend, HealthChoice Illinois:

- Jun 2023 peak: 2,982,596
- Aug 2026: 2,253,817
- Decline: 728,779 (-24.4%) over three years
- 2026: declined through May, then flat at roughly 2.25M

## Architecture

Cloud routine in `github.com/ASP-AI-FQHC/agents`, folder `il-medicaid-tracker/`,
following the established pattern: `PROMPT.md` as source of truth, `sources.json` for
watch targets, `state.json` for history. The routine bootstraps its own files because
this Mac has no GitHub push credentials.

**Prerequisite:** the cloud environment egress allowlist must include
`hfs.illinois.gov`, `data.medicaid.gov`, `download.medicaid.gov`, `www.medicaid.gov`.
As of 2026-09-18 the environment blocks all outbound HTTP including these domains,
which would make the tracker non-functional. Guy is adding them.

### Job 1 — the numbers

1. Fetch the HFS index page and discover monthly PDF links (filenames are unstable:
   `082026aggenrreport.pdf`, `072026aereport.pdf`, `march2026aggenr.pdf` — never
   construct URLs by pattern).
2. Parse each new PDF to county-level totals per program.
3. Append to `state.json`; compute month-over-month deltas.
4. Cross-check direction against the CMS statewide dataset.

### Job 2 — the cliff watch

Diff these weekly and alert on any change:

- HFS Report Center index — a resumed Eligibility Redetermination Report is the
  highest-value event this tracker can catch; that is when termination counts become public
- `/info/fedresctr/` — currently publishing HR1 work-requirement toolkits
- "Changes Coming to Medicaid" page
- IDHS work requirements page

## Parser design

Proven against 24 months spanning four format eras; all 24 parsed with county counts
and stated totals matching.

Format drift is the primary technical risk:

- Program renamed MMAI to FIDE-SNP; normalize both to `dual`
- MCO columns appear and disappear (IlliniCare, BCBS) across years
- Section headers sometimes missing after a page break (April 2025)
- Suppressed small cells marked `*` ("Counties with minimal enrollment are not
  included in the total due to HIPAA")

Mitigations:

- Match county names against a canonical 102-county list rather than column position
- Take the **last** numeric on a row as the county total; per-MCO detail is
  nice-to-have and not relied upon
- `Grand Total` terminates a section; orphaned rows buffer and are claimed by the
  next continuation header
- **Conflict detection:** a county appearing twice in one section means
  mis-attribution. Flag and refuse rather than report.
- Validate row sum against the stated Grand Total within 0.5%. The stated total is
  authoritative when present.

## Failure behavior

If a source is unreachable, a PDF layout changes, or validation fails, the email says
so explicitly. It never reports a parsed zero as real. A county dropping to zero
because of a broken parse would be worse than no email.

## Cadence

Weekly, Mondays. Data is monthly but publication dates wobble (August data landed
September 2), so weekly catches it within days. Escalate to twice-weekly once inside
the January 2027 redetermination window.

## Report format

1. Headline: statewide total, change since last month, change since pre-cliff baseline
2. Counties ranked by decline — largest losses first, absolute and percent
3. Counties gaining, briefly
4. Cliff-watch alerts, if any
5. Standing caveat: managed care only, ~2.25M of ~3.26M
6. Sources and the data month, so figures are never mistaken for current-day

## Open items

- Verify whether zip-level data is Cook County only (unconfirmed claim) before relying
  on it for service-area baselines
- Confirm egress allowlist change took effect; first run should report source reachability
