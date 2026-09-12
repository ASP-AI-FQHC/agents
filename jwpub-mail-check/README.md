# jwpub-mail-check

Daily cloud routine (Claude Code) that logs in to Guy Fuller's mail.jwpub.org
mailbox, finds unread messages not reported before, and emails a summary to
guyfuller@guyfuller.com and gfuller@allstarpartners.com.

- `PROMPT.md` — the agent's instructions (source of truth).
- `check-inbox.js` — Playwright script that performs the login, handles the
  emailed two-step code via `code.txt`, and writes `out.json`.
- `seen.json` — ids already reported; committed by the routine after each run.
- Requires `JWPUB_PASSWORD` set on the cloud environment. Never commit it.

Runs at 23:00 UTC (6 PM Chicago during daylight time, 5 PM during standard time).
