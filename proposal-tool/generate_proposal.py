#!/usr/bin/env python3
"""
ALLSTAR Partners — ASP Secure IT proposal generator.

Fills the branded Word template (template.docx) from a proposal JSON file and
writes a ready-to-send .docx. All ALLSTAR formatting (cover, fonts, colors,
Table of Contents, price-table layout, signature block, Exhibit A) comes from
the template unchanged — only client content and pricing are inserted.

Usage:
    python generate_proposal.py <input.json> [output.docx]

The input JSON uses the same shape your pricing tool already produces:

    {
      "meta": {
        "clientName": "Any FQHC",
        "proposalId": "SPR-82MVA4",
        "proposalDate": "2026-08-11",      # ISO; rendered as MM/DD/YYYY
        "expirationDate": "2026-09-10",
        "serviceTermMonths": 36,
        "nrc": "Waived",
        "tmHourlyRate": 175
      },
      "lines": [
        {"service":"...Managed Site","perUnit":"Site","qty":4,"unitPrice":507.80,"monthly":2031.20},
        ... one line per unit type: Site, User, Identity, Device, Workstation, Server ...
      ],
      "totals": { "monthlyTotal": 26582.42 }   # optional; computed from lines if absent
    }

Internal-only fields (unitCost, margin, blendedMargin, annualTotal) are ignored —
they never appear on a client-facing proposal.
"""
import sys, json, zipfile, shutil, re, os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template.docx")

UNITS = ["Site", "User", "Identity", "Device", "Workstation", "Server"]

def die(msg):
    print("ERROR:", msg); sys.exit(1)

def xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))

def money(v):
    return "${:,.2f}".format(float(v))

def num2(v):                      # unit price shown inside "($X/Unit)"
    return "{:,.2f}".format(float(v))

def fmt_date(s):
    s = str(s).strip()
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, f).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return s   # leave as-is if unrecognised

def main():
    if len(sys.argv) < 2:
        die("usage: python generate_proposal.py <input.json> [output.docx]")
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    meta = data.get("meta", {})
    lines = data.get("lines", [])

    by_unit = {}
    for ln in lines:
        u = str(ln.get("perUnit", "")).strip().capitalize()
        by_unit[u] = ln
    missing = [u for u in UNITS if u not in by_unit]
    if missing:
        die(f"input is missing line item(s) for: {', '.join(missing)}")

    monthly_total = data.get("totals", {}).get("monthlyTotal")
    if monthly_total is None:
        monthly_total = sum(float(by_unit[u]["monthly"]) for u in UNITS)

    prep_name  = meta.get("preparedByName", "Guy Fuller")
    prep_email = meta.get("preparedByEmail", "gfuller@allstarpartners.com")

    repl = {
        "{{CLIENT_NAME}}":          xml_escape(meta.get("clientName", "Client")),
        "{{PREPARED_BY_NAME}}":     xml_escape(prep_name),
        "{{PREPARED_BY_EMAIL}}":    xml_escape(prep_email),
        "{{PROPOSAL_ID}}":          xml_escape(meta.get("proposalId", "")),
        "{{PROPOSAL_DATE}}":        xml_escape(fmt_date(meta.get("proposalDate", ""))),
        "{{EXPIRATION_DATE}}":      xml_escape(fmt_date(meta.get("expirationDate", ""))),
        "{{NRC}}":                  xml_escape(meta.get("nrc", "Waived")),
        "{{TM_RATE}}":              xml_escape(meta.get("tmHourlyRate", "")),
        "{{SERVICE_TERM_MONTHS}}":  xml_escape(meta.get("serviceTermMonths", "")),
        "{{MONTHLY_TOTAL}}":        money(monthly_total),
    }
    for u in UNITS:
        ln = by_unit[u]
        U = u.upper()
        repl[f"{{{{QTY_{U}}}}}"]  = xml_escape(ln["qty"])
        repl[f"{{{{UNIT_{U}}}}}"] = num2(ln["unitPrice"])
        repl[f"{{{{COST_{U}}}}}"] = money(ln["monthly"])

    # Files that carry tokens: the body, and the relationships file (mailto hyperlink).
    token_files = ("word/document.xml", "word/_rels/document.xml.rels")
    filled = {}
    with zipfile.ZipFile(TEMPLATE) as z:
        for name in token_files:
            s = z.read(name).decode("utf-8")
            for tok, val in repl.items():
                s = s.replace(tok, val)
            filled[name] = s

    left = sorted(set(re.findall(r"{{[A-Z_]+}}", "".join(filled.values()))))
    if left:
        die(f"unfilled tokens remain (bad/missing input): {left}")

    out = sys.argv[2] if len(sys.argv) > 2 else \
        f"ASP_Secure_IT_Proposal_{re.sub(r'[^A-Za-z0-9]+','_', meta.get('clientName','Client'))}.docx"

    shutil.copyfile(TEMPLATE, out)
    tmp = out + ".tmp"
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            b = filled[item.filename].encode("utf-8") if item.filename in filled \
                else zin.read(item.filename)
            zout.writestr(item, b)
    os.replace(tmp, out)
    print(f"Wrote {out}")
    print(f"  Client: {meta.get('clientName')}   Proposal: {meta.get('proposalId')}   "
          f"Monthly: {money(monthly_total)}")
    print("  Open in Word, then Ctrl+A -> F9 to refresh the Table of Contents / page numbers.")

if __name__ == "__main__":
    main()
