# FQHC Breach & Advisory Watch

A scheduled cloud agent for **Allstar Partners** that watches new public HHS
OCR breach disclosures, OCR enforcement, and authoritative healthcare security
advisories (CISA, HHS, FBI, Health-ISAC, HRSA) for developments involving
federally qualified health centers. It alerts only when a verified item bears
on Allstar's identity security, vulnerability management, or HIPAA services.

There is no server to run. A Claude Code cloud routine clones this
repo every morning, follows `PROMPT.md`, and commits `seen.json` back.

| File | Purpose |
| --- | --- |
| `PROMPT.md` | The routine's full instructions. |
| `sources.json` | Every URL checked, the web-search fallback queries, and the recipient list. Edit to add a source or recipient. |
| `ocr_portal.js` | Reads the HHS OCR breach portal with headless Chromium and prints the newest rows as JSON. Run by the routine; `node fqhc-breach-watch/ocr_portal.js 2`. |
| `seen.json` | The routine's memory: every item already reported (or checked and rejected), and a log of the last 30 runs. |

## Schedule and delivery

- Daily, 6:37 AM Central.
- Alerts are emailed to `sources.json` → `recipients` from Guy Fuller's
  connected Gmail (subject starts `[FQHC Watch]`) and pushed to his phone.
- Quiet days send nothing. Check `seen.json` → `runs` to confirm it ran.

## Resetting

Set `"initialized": false` and empty `items` in `seen.json`, commit, and run
the routine. The next run sends one 30-day baseline summary instead of an
alert.
