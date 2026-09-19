"""Compose the email body.

Failures are surfaced at the top, never swallowed: a silently wrong figure
is worse than an acknowledged gap.
"""
from .counties import display

CAVEAT = (
    "Source: HFS Detailed Managed Care Enrollment. This covers MANAGED CARE ONLY "
    "(about 2.25M of roughly 3.26M total Illinois Medicaid enrollees). It is a churn "
    "and trend signal, not a total headcount."
)

CONTEXT = (
    "Context: all ACA adults move to 6-month redeterminations with work requirements "
    "starting January 1, 2027. Estimates of Illinois coverage loss range from 270,000 "
    "to over 700,000."
)


def subject(month, mom_change, alerts):
    flag = "ALERT: " if alerts else ""
    direction = f"{mom_change:+,}" if mom_change else "no change"
    return f"{flag}IL Medicaid {month} — {direction} MoM"


def compose(month, total, mom, ranked, alerts, problems, baseline=None):
    L = []
    if problems:
        L.append("DATA PROBLEMS THIS RUN — figures below may be incomplete:")
        L += [f"  - {p}" for p in problems]
        L.append("")
    if alerts:
        L.append("CLIFF WATCH — new items detected:")
        L += [f"  - {a}" for a in alerts]
        L.append("")

    L += [f"Data month: {month}",
          f"Statewide managed-care enrollment: {total:,}",
          f"Change vs prior month: {mom:+,}"]
    if baseline:
        L.append(f"Change vs {baseline['baseline']} baseline: "
                 f"{baseline['change']:+,} ({baseline['pct']:+.1f}%)")
    L.append("")

    losers = [r for r in ranked if r["change"] < 0][:15]
    if losers:
        L.append("LARGEST DECLINES BY COUNTY")
        for r in losers:
            L.append(f"  {display(r['county']):<14} {r['then']:>9,} -> {r['now']:>9,}  "
                     f"{r['change']:>+7,} ({r['pct']:+.1f}%)")
        L.append("")

    gainers = [r for r in ranked if r["change"] > 0]
    if gainers:
        L.append("COUNTIES GAINING")
        for r in sorted(gainers, key=lambda x: -x["change"])[:5]:
            L.append(f"  {display(r['county']):<14} {r['change']:>+7,} ({r['pct']:+.1f}%)")
        L.append("")

    L += [CAVEAT, "", CONTEXT]
    return "\n".join(L)
