"""Compose the email body.

Failures are surfaced at the top, never swallowed: a silently wrong figure
is worse than an acknowledged gap.
"""
import html as _html

from .counties import display

TITLE = "Illinois Medicaid Cliff Tracker"
BYLINE = "Brought to you by ALLSTAR Partners"   # only ALLSTAR is capitalised
ABOUT = (
    "A monthly read on Medicaid managed-care enrollment across all 102 Illinois "
    "counties, showing where coverage is slipping ahead of the January 1, 2027 work "
    "requirements. It also flags new state redetermination reports and policy "
    "guidance as they are posted."
)

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


def _county_counts(ranked):
    down = sum(r["change"] < 0 for r in ranked)
    up = sum(r["change"] > 0 for r in ranked)
    return (f"{down} declined, {up} gained, {len(ranked) - down - up} flat, "
            f"of {len(ranked)}")


def compose(month, total, mom, ranked, alerts, problems, baseline=None):
    L = [TITLE, BYLINE, "", ABOUT, ""]
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
    if ranked:
        L.append(f"Counties: {_county_counts(ranked)}")
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


# --- HTML body ---------------------------------------------------------------
# Same report, same figures, same order as compose(). Inline styles and tables
# only: mail clients strip <style> blocks and ignore classes. Everything that
# originated on a web page (problems, alerts) is escaped.

_FONT = "font-family:Arial,Helvetica,sans-serif;"
_DOWN, _UP, _MUTED = "#b42318", "#067647", "#667085"
_BRAND = "#0094bb"                               # ALLSTAR Partners teal
_TD = "padding:6px 10px;border-bottom:1px solid #eaecf0;"
_TH = ("padding:6px 10px;border-bottom:2px solid #d0d5dd;font-size:12px;"
       "color:#667085;text-transform:uppercase;")


def _e(s):
    return _html.escape(str(s), quote=False)


def _colour(n):
    return _DOWN if n < 0 else _UP if n > 0 else _MUTED


def _box(title, items, fg, bg):
    lis = "\n".join(f"<li>{_e(i)}</li>" for i in items)
    return (f'<div style="background:{bg};border-left:4px solid {fg};'
            f'padding:10px 14px;margin:0 0 16px 0;">\n'
            f'<div style="font-weight:bold;color:{fg};">{title}</div>\n'
            f'<ul style="margin:6px 0 0 0;padding-left:18px;">\n{lis}\n</ul>\n</div>')


def _table(title, head, rows):
    ths = "".join(f'<th align="{a}" style="{_TH}">{h}</th>' for h, a in head)
    return (f'<div style="font-weight:bold;margin:20px 0 6px 0;">{title}</div>\n'
            f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;font-size:14px;">\n<tr>{ths}</tr>\n'
            + "\n".join(rows) + "\n</table>")


def _cell(text, align="right", colour=None):
    style = _TD + (f"color:{colour};" if colour else "")
    return f'<td align="{align}" style="{style}">{text}</td>'


def compose_html(month, total, mom, ranked, alerts, problems, baseline=None):
    P = [f'<div style="border-top:4px solid {_BRAND};padding-top:12px;font-size:20px;'
         f'font-weight:bold;color:{_BRAND};">{_e(TITLE)}</div>',
         f'<div style="font-size:13px;color:{_MUTED};">{_e(BYLINE)}</div>',
         f'<div style="font-size:14px;margin:10px 0 18px 0;">{_e(ABOUT)}</div>']
    if problems:
        P.append(_box("DATA PROBLEMS THIS RUN — figures below may be incomplete",
                      problems, "#b54708", "#fffaeb"))
    if alerts:
        P.append(_box("CLIFF WATCH — new items detected", alerts, _DOWN, "#fef3f2"))

    P.append(f'<div style="font-size:13px;color:{_MUTED};">Data month: {_e(month)}</div>')
    P.append('<div style="font-size:13px;margin-top:10px;">'
             'Statewide managed-care enrollment</div>')
    P.append(f'<div style="font-size:30px;font-weight:bold;">{total:,}</div>')
    P.append(f'<div style="margin-top:4px;">Change vs prior month: '
             f'<b style="color:{_colour(mom)};">{mom:+,}</b></div>')
    if baseline:
        P.append(f'<div>Change vs {_e(baseline["baseline"])} baseline: '
                 f'<b style="color:{_colour(baseline["change"])};">'
                 f'{baseline["change"]:+,} ({baseline["pct"]:+.1f}%)</b></div>')
    if ranked:
        P.append(f'<div style="color:{_MUTED};">Counties: {_county_counts(ranked)}</div>')

    losers = [r for r in ranked if r["change"] < 0][:15]
    if losers:
        rows = ["<tr>" + _cell(_e(display(r["county"])), "left")
                + _cell(f'{r["then"]:,}') + _cell(f'{r["now"]:,}')
                + _cell(f'{r["change"]:+,}', colour=_DOWN)
                + _cell(f'{r["pct"]:+.1f}%', colour=_DOWN) + "</tr>" for r in losers]
        P.append(_table("LARGEST DECLINES BY COUNTY",
                        [("County", "left"), ("Prior", "right"), ("Now", "right"),
                         ("Change", "right"), ("%", "right")], rows))

    gainers = sorted((r for r in ranked if r["change"] > 0), key=lambda x: -x["change"])[:5]
    if gainers:
        rows = ["<tr>" + _cell(_e(display(r["county"])), "left")
                + _cell(f'{r["change"]:+,}', colour=_UP)
                + _cell(f'{r["pct"]:+.1f}%', colour=_UP) + "</tr>" for r in gainers]
        P.append(_table("COUNTIES GAINING",
                        [("County", "left"), ("Change", "right"), ("%", "right")], rows))

    note = f"font-size:12px;color:{_MUTED};margin-top:"
    P.append(f'<div style="{note}24px;">{_e(CAVEAT)}</div>')
    P.append(f'<div style="{note}10px;">{_e(CONTEXT)}</div>')

    return (f'<div style="{_FONT}color:#101828;background:#ffffff;max-width:640px;'
            f'font-size:15px;line-height:1.45;">\n' + "\n".join(P) + "\n</div>\n")
