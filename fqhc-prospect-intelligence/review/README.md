# review

The daily review routine for FQHC Prospect Intelligence.

- `PROMPT.md` — the agent's instructions, and the source of truth. The
  scheduled task carries only a pointer to this file, so changing the routine
  means editing this file, not the schedule.
- `seen.json` — findings already reported, so the same thing is not raised
  every morning. Written by the routine after each run.

Runs weekdays at 05:41 America/Chicago. Report only; it changes no application
code. A day with nothing new is a correct outcome and produces one line.

The rotation covers a different dimension each weekday — integrity, parsers,
docs and tests, performance, interface — so the whole application is swept once
a week without reviewing everything every day.

To pause it, disable the scheduled task rather than deleting this folder; to
change what it looks at, edit the rotation table in `PROMPT.md`.
