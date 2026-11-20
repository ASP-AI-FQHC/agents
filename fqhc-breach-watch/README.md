# FQHC Breach & Advisory Watch

A scheduled cloud agent for **Allstar Partners** that watches new public HHS
OCR breach disclosures, OCR enforcement, and authoritative healthcare security
advisories (CISA, HHS, FBI, Health-ISAC, HRSA) for developments involving
federally qualified health centers. It alerts only when a verified item bears
on Allstar's identity security, vulnerability management, or HIPAA services.

Since 2026-11-20 it also watches FQHCs for sales signals: leadership changes,
open RFPs, security and technology funding, and technology initiatives. For
each FQHC with a breach or signal it scores the account (ALLSTAR), builds an
executive profile, names the business problems the evidence points to, and
drafts outreach for Guy to review. Signals land on the tracker's Signals tab;
accounts on the Prospects tab.

There is no server to run. A Claude Code cloud routine clones this
repo every morning, follows `PROMPT.md`, and commits `seen.json` back.

| File | Purpose |
| --- | --- |
| `PROMPT.md` | The routine's full instructions. |
| `sources.json` | Every URL checked, the web-search fallback queries, and the recipient list. Edit to add a source or recipient. |
| `ocr_portal.js` | Reads the HHS OCR breach portal with headless Chromium and prints the newest rows as JSON. Run by the routine; `node fqhc-breach-watch/ocr_portal.js 2`. |
| `state_lists.js` | Reads the Texas, Oregon, Indiana and Wisconsin breach lists and prints filings since a date as JSON. `node fqhc-breach-watch/state_lists.js 2026-11-01`. |
| `seen.json` | The routine's memory: every item already reported (or checked and rejected), and a log of the last 30 runs. |

## Schedule and delivery

- Daily, 5:36 AM Central.
- Alerts are emailed to `sources.json` → `recipients` from Guy Fuller's
  connected Gmail (subject starts `[FQHC Watch]`) and pushed to his phone.
- Quiet days send a short "Nothing new" email with the tracker link.

## Resetting

Set `"initialized": false` and empty `items` in `seen.json`, commit, and run
the routine. The next run sends one 30-day baseline summary instead of an
alert.
