# RHTP Award Tracker — design

Date: 2026-09-10. Owner: Guy Fuller, Allstar Partners.

## Goal

Alert gfuller@allstarpartners.com and jdudek@allstarpartners.com by email
whenever Arkansas, Illinois, Indiana, or Wisconsin releases Rural Health
Transformation Program money downstream: sub-award lists, NOFOs/RFPs,
deadlines, vendor solicitations, or CMS actions affecting those states.
Silent on quiet days. Report everything, flagged by relevance to Allstar
Partners' technology and FQHC practice.

## Decisions

- Runtime: Claude Code cloud routine, weekday mornings, Sonnet 5, repo
  `ASP-AI-FQHC/agents` cloned as the source, Gmail connector for sending.
- Scope: state-level flow only (not CMS-to-state disbursements nationwide).
- One routine for all four states, one grouped email per run.
- State: `rhtp-award-tracker/seen.json` committed back by the routine.
  Fallback if push fails: email still sends and says the push failed.
- Baseline: first run records everything visible and sends one summary email.
- Prompt lives in the repo (`PROMPT.md`); the routine's event message points
  to it so behavior is changed by commit, not by editing the routine.

## Rejected alternatives

- Python scraper wrapped by the routine: more robust diffs, but every state
  site is a CMS-driven WordPress or Drupal page that changes layout often;
  maintenance cost outweighs benefit at a few items per month.
- Local launchd script: only runs while the Mac is awake; user chose cloud.
- Gmail-only dedupe (searching sent mail): no git dependency, but reading a
  growing sent-mail history each run is slower and less inspectable than a
  JSON file.

## Testing

Run the routine once manually after creation. Success: baseline email arrives
at both addresses, `seen.json` is committed with `initialized: true`, and the
run log shows every source fetched or explicitly reported as failed.
