# ASP Proposal Builder

Generate branded **ALLSTAR Partners — ASP Secure IT®** managed-services proposals from pricing
data. Two ways to use it, both producing the identical Word document:

- **`index.html`** — a self-contained web form (no server, no install). Fill it in and download
  the `.docx`, or upload/download the underlying JSON.
- **`generate_proposal.py`** — a command-line generator for batch/automation.

All ALLSTAR formatting (cover, fonts, colors, Table of Contents, price-table layout, signature
block, Exhibit A) lives in `template.docx`; only client content and pricing change.

## For the team — how to use it

Pick whichever fits:

1. **Open the web form.** Download `index.html` from this repo (**Code → download**, or clone) and
   double-click it — it opens in any browser and works offline. Fill in the client, preparer,
   dates, and quantities, then **Download Word (.docx)**.
2. **Share the file.** Because `index.html` is fully self-contained, you can email it or drop it on
   a shared drive / SharePoint; teammates just open it in a browser.
3. **Host it (optional).** It's a single static file, so any static host works (internal web
   server, SharePoint, or GitHub Pages if this repo is made public / on a plan that supports
   private Pages). Everyone then bookmarks one URL.

After opening the generated `.docx` in Word, press **Ctrl+A → F9** to refresh the Table of
Contents and page numbers.

## Files

| File | Purpose |
|------|---------|
| `index.html` | Self-contained web interface (template + logo + zip engine embedded). Upload JSON / fill form → download `.docx` or JSON. |
| `generate_proposal.py` | CLI: fills the template from a JSON file and writes a `.docx` (Python 3 stdlib only). |
| `template.docx` | The approved ALLSTAR proposal with `{{TOKENS}}`. **Formatting lives here.** |
| `template_connectsecure.docx` | The approved ConnectSecure proposal, tokenised the same way. |
| `sample_input.json` | Example input (the "Any FQHC" proposal). Copy it to start a new one. |
| `sample_input_connectsecure.json` | Example input for a ConnectSecure proposal. |

## Command line

```bash
python generate_proposal.py sample_input.json
# or name the output
python generate_proposal.py my_client.json ACME_Health_Proposal.docx
# ConnectSecure
python generate_proposal.py sample_input_connectsecure.json
```

## Input JSON

The same shape the web form imports/exports and your pricing tool can emit:

```json
{
  "meta": {
    "clientName": "Any FQHC",
    "preparedByName": "Guy Fuller",
    "preparedByEmail": "gfuller@allstarpartners.com",
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
- One `lines` entry per unit type: **Site, User, Identity, Device, Workstation, Server**.
- `preparedByName` / `preparedByEmail` are optional (default Guy Fuller / gfuller@allstarpartners.com);
  the email renders as a clickable `mailto:` link.
- Three prose sections are optional and editable in the web form (each with **Reset to default**): `executiveOverview` (page-1 narrative), `proposalSummary` (client-description intro), and `additionalTerms` (one term per line). Omit them for the default wording with the client name filled in.
- `productLine` sets the brand in every heading — **ASP Healthcare IT**, **ASP Enterprise IT**, or **ASP Business IT** (default Healthcare). In the web form it's the **Product line** dropdown.
- `productLine` also selects **ASP Healthcare IT - ConnectSecure**, which is a *different proposal*, not a different name on the same one — see below.
- Dates accept `YYYY-MM-DD` (rendered `MM/DD/YYYY`).
- `totals.monthlyTotal` is optional — computed from the line items if omitted.
- Internal-only fields (`unitCost`, `margin`, `blendedMargin`) are ignored; the proposal is
  client-facing only.

## Updating the template

If the approved proposal design changes, edit `template.docx` in Word (keep the `{{TOKENS}}`
intact) and re-commit it — and re-embed it into `index.html` if you want the web form updated too.

## ConnectSecure

**ASP Healthcare IT — ConnectSecure** is the fourth entry in the **Product line**
dropdown. Unlike the other three, it does not just change the brand in the
headings: it swaps the whole document to the approved ConnectSecure proposal —
its own Executive Overview, Scope of Work, Scope of Services and Onboarding
Schedule, and a price table with one monthly line instead of six unit types.

Selecting it changes the form to match:

| | Managed Services | ConnectSecure |
|---|---|---|
| Price table | Site, User, Identity, Device, Workstation, Server | one subscription line |
| Default price | list prices per unit | $500.00/month, qty 1 |
| Onboarding (NRC) | Waived | $500.00 |
| Service term | 36 months | 12 months |
| T&M rate | $175/hr. | $195.00 per hour |
| Template | `template.docx` | `template_connectsecure.docx` |

Quantity, price, term and rate all stay editable — the values above are just
the defaults, and anything you have already typed is left alone when you switch.
Proposal Summary and Additional Terms are hidden because the ConnectSecure
document does not have those sections; the Executive Overview is editable as
usual and defaults to the approved wording with the client name filled in.

In JSON, `productLine` carries it, and the single line item uses
`"perUnit": "ConnectSecure"`:

```json
{
  "meta": { "productLine": "ASP Healthcare IT - ConnectSecure", "nrc": 500,
            "serviceTermMonths": 12, "tmHourlyRate": 195 },
  "lines": [ { "perUnit": "ConnectSecure", "qty": 1,
               "unitPrice": 500.00, "monthly": 500.00 } ]
}
```

`template_connectsecure.docx` is the signed-off ConnectSecure proposal with the
client-specific values replaced by `{{TOKENS}}` — the cover, fonts, colours,
Table of Contents, price tables, signature block and Exhibit A are untouched
from the original. Edit it in Word the same way as `template.docx`, keeping the
tokens intact, and re-embed it into `index.html` if the web form should pick the
change up.
