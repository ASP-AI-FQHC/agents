---
name: exec-meeting-agenda
description: Quickly build a polished, time-boxed agenda for executive-level meetings (board, leadership team, client C-suite, QBR, steering committee, compliance/security review). Use when the user says "agenda for", "build an agenda", "exec meeting agenda", "prep the leadership meeting", or pastes meeting notes/goals and wants an agenda.
---

# Executive Meeting Agenda

Goal: usable agenda in one pass. Minimal questions. Decision-oriented. One page.

## Step 1 — Gather inputs (ask only what is missing, max 1 round)

Infer from the request, calendar invite, or pasted notes first. Needed:

1. Meeting purpose / desired outcome (decide, align, inform, review)
2. Date, time, length, location/link
3. Attendees + roles; chair/facilitator
4. Topics or items to cover; decisions needed
5. Pre-reads or data available

If the user gives little, draft with sensible defaults and mark assumptions with `[confirm]`. Do not stall on questions.

Optional: if Calendar/Gmail/Drive tools are connected and the user names a meeting or thread, pull attendees, time, and prior notes from there.

## Step 2 — Build the agenda

Rules:
- Lead with the **desired outcome** — one sentence, starting with a verb.
- Order: decisions first while energy is high; updates last; always end with action items.
- Every item gets: **time box**, **owner**, **type** (Decide / Discuss / Inform), and a one-line **objective or ask**.
- Total time = meeting length, with 5–10% buffer. Default 45 or 60 min; 3–6 items max.
- Push pure status updates to a pre-read. Do not spend exec time reading slides aloud.
- Flag decisions needed in a separate "Decisions requested" list.
- Include pre-reads with links/owners and due date (ideally 24–48h ahead).
- Compliance / cybersecurity context: surface risk posture, audit status, open findings, incidents, and budget asks as distinct items; avoid jargon, lead with business impact.

## Step 3 — Output format

```
# [Meeting Title]
**Date/Time:** … | **Location/Link:** … | **Facilitator:** … | **Notes:** …
**Attendees:** Name (Role), …

## Purpose & Desired Outcome
One to two sentences.

## Agenda
| Time | Item | Type | Owner | Objective / Ask |
|------|------|------|-------|-----------------|
| 0:00–0:05 | Welcome & objectives | Inform | Chair | Confirm outcome |
| … | … | Decide | … | … |
| 0:55–1:00 | Decisions recap & next steps | Decide | Chair | Confirm owners + dates |

## Decisions Requested
1. …

## Pre-Reads (due [date])
- … (owner)

## Action Items (to complete live)
| Action | Owner | Due |
|--------|-------|-----|
```

## Step 4 — Deliver and offer

- Show the agenda in chat, concise.
- Offer next steps in one line: save as Doc/Docx, draft the invite email, attach to calendar event, or generate a follow-up/minutes template.
- Only create files, send emails, or edit calendar events when asked.

## Style

Polished business tone, warm and professional, trusted-advisor voice. Short sentences. No filler. Action-oriented verbs. Defined acronyms on first use.
