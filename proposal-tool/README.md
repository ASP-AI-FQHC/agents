# ASP Secure IT® Proposal Generator

A small, permanent tool that turns a proposal's **data** (client + pricing) into a
finished, **ALLSTAR Partners–branded** Microsoft Word proposal — with the exact cover,
fonts, colors, Table of Contents, price-table layout, signature block, and Exhibit A
from the approved sample proposal.

Only the client content and pricing change; **all formatting comes from the template**,
so every proposal looks identical to the approved sample.

## Files

| File | Purpose |
|------|---------|
| `template.docx` | The approved ALLSTAR proposal with `{{TOKENS}}` where client/pricing data goes. **Formatting lives here.** |
| `generate_proposal.py` | Fills the template from a JSON file and writes a ready-to-send `.docx`. |
| `sample_input.json` | Example input (the "Any FQHC" proposal, SPR-82MVA4). Copy it to start a new one. |

## The process (data → proposal)

```
  ┌────────────────┐     ┌──────────────────┐     ┌────────────────────┐     ┌───────────────┐
  │ Pricing tool   │ ──▶ │ proposal .json   │ ──▶ │ generate_proposal  │ ──▶ │ branded .docx │
  │ (quantities +  │     │ (client + lines) │     │ .py  +  template   │     │ (send/print)  │
  │  unit prices)  │     └──────────────────┘     └────────────────────┘     └───────────────┘
```

1. **Get the numbers.** Your pricing tool already outputs the quantities, unit prices, and
   monthly costs per service line (that is the JSON shown below).
2. **Save them as a JSON file** in the shape of `sample_input.json` (copy it and edit the values).
3. **Run the generator** (see below). It produces `ASP_Secure_IT_Proposal_<Client>.docx`.
4. **Open the .docx in Word** and press **Ctrl+A → F9** to refresh the Table of Contents and
   page numbers.
5. **Review the narrative.** The Executive Overview / Proposal Summary use industry-neutral
   wording with the client's name filled in — skim and tailor a sentence if the deal calls for it.
6. **Send it.** Save as PDF from Word if you need a PDF.

## Running it

Requires only Python 3 (standard library — no packages to install).

```bash
# from this folder
python generate_proposal.py sample_input.json
# or specify an output name
python generate_proposal.py my_client.json ACME_Health_Proposal.docx
```

## Input format

Use the same JSON your pricing tool produces. Minimum required fields:

```json
{
  "meta": {
    "clientName": "Any FQHC",
    "proposalId": "SPR-82MVA4",
    "proposalDate": "2026-08-11",
    "expirationDate": "2026-09-10",
    "serviceTermMonths": 36,
    "nrc": "Waived",
    "tmHourlyRate": 175
  },
  "lines": [
    { "perUnit": "Site",        "qty": 4,   "unitPrice": 507.80, "monthly": 2031.20 },
    { "perUnit": "User",        "qty": 300, "unitPrice": 4.58,   "monthly": 1374.00 },
    { "perUnit": "Identity",    "qty": 350, "unitPrice": 6.26,   "monthly": 2191.00 },
    { "perUnit": "Device",      "qty": 350, "unitPrice": 37.75,  "monthly": 13212.50 },
    { "perUnit": "Workstation", "qty": 300, "unitPrice": 23.48,  "monthly": 7044.00 },
    { "perUnit": "Server",      "qty": 12,  "unitPrice": 60.81,  "monthly": 729.72 }
  ],
  "totals": { "monthlyTotal": 26582.42 }
}
```

Notes:
- One `lines` entry is required for each of the six unit types:
  **Site, User, Identity, Device, Workstation, Server**.
- Dates accept `YYYY-MM-DD` (rendered as `MM/DD/YYYY`).
- `totals.monthlyTotal` is optional — if omitted it is summed from the line items.
- Internal-only fields (`unitCost`, `margin`, `blendedMargin`, `annualTotal`) are **ignored**;
  they never appear on the client-facing proposal.
- The generator **fails loudly** if a line item is missing or a token is left unfilled, so a
  bad input can't silently produce a broken proposal.

## What is fixed vs. variable

- **Fixed by the template:** all branding/formatting, the service descriptions, Scope of Work,
  Scope of Services, terms, acceptance language, Exhibit A, and the preparer
  (Guy Fuller / gfuller@allstarpartners.com — the email is a Word hyperlink, so change the
  preparer by editing the cover in Word if ever needed).
- **Variable (from JSON):** client name, proposal ID, dates, service term, NRC, T&M rate,
  and the six line items (quantity, unit price, monthly cost) plus the monthly total.

## Updating the template

If the approved proposal design changes, edit `template.docx` in Word (keep the `{{TOKENS}}`
intact) and re-commit it. The generator needs no changes.
