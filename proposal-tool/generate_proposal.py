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
import sys, json, zipfile, shutil, re, os, time
from datetime import datetime, timedelta, date

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template.docx")
TEMPLATE_CS = os.path.join(HERE, "template_connectsecure.docx")

UNITS = ["Site", "User", "Identity", "Device", "Workstation", "Server"]

# Product-line brand shown in every heading (cover, summary, SOS, price table).
BRANDS = ["ASP Healthcare IT", "ASP Enterprise IT", "ASP Business IT"]
DEFAULT_BRAND = BRANDS[0]

# ConnectSecure is a different proposal, not a different name on the same one:
# a single monthly line instead of six unit types, its own scope sections, and
# a fixed onboarding cost. Selecting it swaps the template.
CONNECTSECURE = "ASP Healthcare IT - ConnectSecure"
CS_BRAND = "ASP Healthcare IT"
CS_NRC = "$500.00"
CS_MONTHLY = 500.00
PRODUCT_LINES = BRANDS + [CONNECTSECURE]

def is_connectsecure(product_line):
    """Whether this proposal is the ConnectSecure one.

    Matched loosely: the web form, saved JSON and hand-written input have all
    spelled it slightly differently, and the cost of a false negative is
    silently generating the wrong document.
    """
    return "connectsecure" in str(product_line or "").replace(" ", "").lower()

# Default Executive Overview — {CLIENT} is replaced with the client name.
# Used when the input JSON has no meta.executiveOverview.
DEFAULT_EXEC = [
    "{CLIENT} operates in a demanding environment where reliability, security, and cost control "
    "are essential to its core mission. As {CLIENT} continues to grow and serve its community, "
    "predictable and dependable technology support is no longer optional—it is foundational to "
    "operational stability, security, and organizational confidence.",
    "ALLSTAR Partners proposes a structured, predictive monthly billing model designed to "
    "eliminate the frustration and uncertainty of variable IT expenses. This approach replaces "
    "reactive, project-based invoicing with a consistent monthly investment that covers a broad "
    "range of technical support and security services. {CLIENT} gains clear financial visibility "
    "while ensuring continuous access to experienced professionals who understand its operational "
    "demands.",
    "ALLSTAR Partners brings a proven track record of delivering reliable, high-value managed "
    "services to organizations that demand accountability, security, and consistency. Our "
    "philosophy is simple: build stability, bake in security, and provide leadership—not just "
    "support. This proposal reflects a long-term partnership focused on operational confidence, "
    "financial predictability, and measurable results.",
]
# Default Executive Overview for ConnectSecure -- the approved wording from the
# signed-off proposal, with the client name parameterised.
DEFAULT_EXEC_CS = [
    "{CLIENT} delivers essential medical, dental, and behavioral healthcare with a mission "
    "grounded in access, dignity, and high-quality care for all, regardless of ability to pay. "
    "Supporting that mission requires more than responsive IT support; it requires a trusted "
    "partner that can align technology with organizational priorities, execute with discipline, "
    "and provide the financial predictability necessary to plan with confidence",
]
# Default Proposal Summary intro paragraph ({CLIENT} -> client name).
DEFAULT_SUMMARY = [
    "{CLIENT} (referred to as “Client”) relies on secure, dependable technology to operate "
    "effectively. ALLSTAR Partners (referred to as “ASP”) is a Cybersecurity-focused Managed "
    "Service Provider (MSP+) with extensive experience in supporting organizations like {CLIENT} "
    "with their technology requirements.",
]
# Default Additional Terms and Conditions (one per item).
DEFAULT_TERMS = [
    "Upon request ASP Engineers will be assigned unique accounts with sufficient access to Client "
    "infrastructure and systems to accomplish the Work or Services in this Proposal. For highly "
    "sensitive tasks, ASP Engineers may work alongside the Client to complete restricted work.",
    "Work and Services will be performed remotely as much as possible.",
    "For ASP engineers working on site the Client is expected to provide a workspace area large "
    "enough to accommodate a laptop with a desk, a chair, electric power and Internet access.",
    "Additional costs may apply if the Client network equipment does not support port mirroring or "
    "an equivalent configuration and active network traffic monitoring via SIEM is required. "
    "Alternatively, the Client may decline to implement active network traffic monitoring if the "
    "equipment cannot be configured to support it.",
    "All emergency recovery services are best effort only without guarantee.",
]
# Paragraph-break fragments: close the current paragraph, open an identically-styled one.
_CALIBRI = '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
BODY_SEP  = ('</w:t></w:r></w:p><w:p><w:pPr><w:rPr>' + _CALIBRI + '</w:rPr></w:pPr>'
             '<w:r><w:rPr>' + _CALIBRI + '</w:rPr><w:t xml:space="preserve">')   # Calibri body
TERMS_SEP = ('</w:t></w:r></w:p><w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
             '<w:r><w:t xml:space="preserve">')                                   # numbered term

def build_paras(text, default_paras, client, sep):
    """Build the XML value for a paragraph region: custom text or the default."""
    if text and str(text).strip():
        paras = [p.strip() for p in re.split(r"\n+", str(text)) if p.strip()]
    else:
        paras = [p.replace("{CLIENT}", client) for p in default_paras]
    if not paras:
        paras = [""]
    return sep.join(xml_escape(p) for p in paras)

def die(msg):
    print("ERROR:", msg); sys.exit(1)

def xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))

_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def new_proposal_id():
    """'SPR-' + the current Unix epoch (seconds) in base 36, e.g. SPR-TKNLKP."""
    n = int(time.time())
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = _B36[r] + out
    return "SPR-" + (out or "0")

# A proposal is dated today and stays valid for this many days, unless the
# input says otherwise.
VALID_DAYS = 30

def parse_date(s):
    """Parse a date in any accepted input format; None if unrecognised/blank."""
    s = str(s or "").strip()
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None

def money(v):
    return "${:,.2f}".format(float(v))

def num2(v):                      # unit price shown inside "($X/Unit)"
    return "{:,.2f}".format(float(v))

def money_or_text(v):
    """Money for a number, verbatim for anything else (e.g. "Waived")."""
    try:
        return money(v)
    except (TypeError, ValueError):
        return str(v)

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

    connectsecure = is_connectsecure(meta.get("productLine"))
    template = TEMPLATE_CS if connectsecure else TEMPLATE

    by_unit = {}
    if connectsecure:
        # One line, priced per subscription. A "ConnectSecure" line in the
        # input wins; otherwise the standard price applies.
        cs_line = next(
            (ln for ln in lines
             if "connectsecure" in str(ln.get("perUnit", "")).replace(" ", "").lower()),
            None,
        ) or {"qty": 1, "monthly": CS_MONTHLY}
        qty = cs_line.get("qty", 1)
        monthly = cs_line.get("monthly")
        if monthly is None:
            monthly = float(cs_line.get("unitPrice", CS_MONTHLY)) * float(qty)
    else:
        for ln in lines:
            u = str(ln.get("perUnit", "")).strip().capitalize()
            by_unit[u] = ln
        missing = [u for u in UNITS if u not in by_unit]
        if missing:
            die(f"input is missing line item(s) for: {', '.join(missing)}")

    monthly_total = data.get("totals", {}).get("monthlyTotal")
    if monthly_total is None:
        monthly_total = float(monthly) if connectsecure else \
            sum(float(by_unit[u]["monthly"]) for u in UNITS)

    proposal_id = str(meta.get("proposalId") or "").strip() or new_proposal_id()
    p_date = parse_date(meta.get("proposalDate")) or date.today()
    e_date = parse_date(meta.get("expirationDate")) or (p_date + timedelta(days=VALID_DAYS))
    prep_name  = meta.get("preparedByName", "Guy Fuller")
    prep_email = meta.get("preparedByEmail", "gfuller@allstarpartners.com")

    repl = {
        "{{CLIENT_NAME}}":          xml_escape(meta.get("clientName", "Client")),
        "{{PREPARED_BY_NAME}}":     xml_escape(prep_name),
        "{{PREPARED_BY_EMAIL}}":    xml_escape(prep_email),
        "{{PROPOSAL_ID}}":          xml_escape(proposal_id),
        "{{PROPOSAL_DATE}}":        xml_escape(p_date.strftime("%m/%d/%Y")),
        "{{EXPIRATION_DATE}}":      xml_escape(e_date.strftime("%m/%d/%Y")),
        "{{NRC}}":                  xml_escape(money_or_text(
                                        meta.get("nrc", CS_NRC if connectsecure else "Waived"))),
        # The ConnectSecure document reads "$195.00 per hour" and the Secure IT
        # one "$175/hr." -- each keeps the form its own signed-off copy uses.
        "{{TM_RATE}}":              xml_escape(
                                        num2(meta.get("tmHourlyRate", 0))
                                        if connectsecure and str(meta.get("tmHourlyRate", "")).strip()
                                        else meta.get("tmHourlyRate", "")),
        "{{SERVICE_TERM_MONTHS}}":  xml_escape(meta.get("serviceTermMonths", "")),
        "{{MONTHLY_TOTAL}}":        money(monthly_total),
        "{{BRAND}}":                xml_escape(
                                        CS_BRAND if connectsecure
                                        else meta.get("productLine", DEFAULT_BRAND)),
        "{{EXEC_OVERVIEW}}":        build_paras(meta.get("executiveOverview"),
                                                DEFAULT_EXEC_CS if connectsecure else DEFAULT_EXEC,
                                                meta.get("clientName", "Client"), BODY_SEP),
        "{{PROPOSAL_SUMMARY}}":     build_paras(meta.get("proposalSummary"), DEFAULT_SUMMARY,
                                                meta.get("clientName", "Client"), BODY_SEP),
        "{{TERMS}}":                build_paras(meta.get("additionalTerms"), DEFAULT_TERMS,
                                                meta.get("clientName", "Client"), TERMS_SEP),
    }
    if connectsecure:
        repl["{{QTY_CS}}"]  = xml_escape(qty)
        repl["{{COST_CS}}"] = money(monthly)
    else:
        for u in UNITS:
            ln = by_unit[u]
            U = u.upper()
            repl[f"{{{{QTY_{U}}}}}"]  = xml_escape(ln["qty"])
            repl[f"{{{{UNIT_{U}}}}}"] = num2(ln["unitPrice"])
            repl[f"{{{{COST_{U}}}}}"] = money(ln["monthly"])

    # Files that carry tokens: the body, and the relationships file (mailto hyperlink).
    token_files = ("word/document.xml", "word/_rels/document.xml.rels")
    filled = {}
    with zipfile.ZipFile(template) as z:
        for name in token_files:
            s = z.read(name).decode("utf-8")
            for tok, val in repl.items():
                s = s.replace(tok, val)
            filled[name] = s

    left = sorted(set(re.findall(r"{{[A-Z_]+}}", "".join(filled.values()))))
    if left:
        die(f"unfilled tokens remain (bad/missing input): {left}")

    stem = "ASP_ConnectSecure_Proposal" if connectsecure else "ASP_Secure_IT_Proposal"
    out = sys.argv[2] if len(sys.argv) > 2 else \
        f"{stem}_{re.sub(r'[^A-Za-z0-9]+','_', meta.get('clientName','Client'))}.docx"

    shutil.copyfile(template, out)
    tmp = out + ".tmp"
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            b = filled[item.filename].encode("utf-8") if item.filename in filled \
                else zin.read(item.filename)
            zout.writestr(item, b)
    os.replace(tmp, out)
    print(f"Wrote {out}")
    print(f"  Client: {meta.get('clientName')}   Proposal: {proposal_id}   "
          f"Monthly: {money(monthly_total)}")
    print("  Open in Word, then Ctrl+A -> F9 to refresh the Table of Contents / page numbers.")

if __name__ == "__main__":
    main()
