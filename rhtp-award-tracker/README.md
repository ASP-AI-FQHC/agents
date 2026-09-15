# RHTP Award Tracker

A scheduled cloud agent for **Allstar Partners** that watches how the federal
$50 billion Rural Health Transformation Program (RHTP) money is being released
inside ten states — Arkansas, Illinois, Indiana, Wisconsin, Missouri,
Tennessee, Mississippi, Louisiana, Texas, and Oklahoma — and emails the team
only when something new appears: a sub-award list, a NOFO or RFP, a deadline,
a vendor solicitation, or a CMS action affecting one of those states.

There is no server and no code to run. A Claude Code cloud routine clones this
repo every weekday morning, follows `PROMPT.md`, and commits `seen.json` back.

| File | Purpose |
| --- | --- |
| `PROMPT.md` | The routine's full instructions. Versioned here so changes are reviewable. |
| `sources.json` | Every URL checked, per state, plus the recipient list and the web-search fallback queries. Edit this to add a source or a recipient. |
| `seen.json` | The routine's memory: every item already reported, and a log of the last 30 runs. Committed by the routine after each run. |

## Schedule and delivery

- Weekday mornings, 7:00 AM Central during daylight time (12:00 UTC). The
  cron is fixed in UTC, so in winter it fires at 6:00 AM Central.
- Alerts go to the addresses in `sources.json` → `recipients`, sent from Guy
  Fuller's connected Gmail. Subject line always starts `[RHTP Tracker]`.
- Quiet days send nothing. Check `seen.json` → `runs` to confirm it ran.

## Managing the routine

The routine lives at https://claude.ai/code/routines. From there it can be
paused, run on demand, or deleted. The prompt in the routine itself is a
one-line pointer to `PROMPT.md`, so edit the file here to change behavior.

## Adding a state

Add the state's block to `sources.json` → `states`, then add its two-letter
code to the `baseline_pending` array in `seen.json` and commit. The next run
snapshots just that state (a baseline section at the top of the email) and
keeps the history of every other state intact. Also add the code to the
state order in `PROMPT.md` under "Email rules".

## Resetting

To re-baseline everything, set `"initialized": false` and empty `items` in
`seen.json`, commit, and run the routine. The next run sends one baseline
summary email instead of an alert.
