"""Month-over-month change, ranked by decline."""
from .history import months


def _prior_of(hist, program, month):
    ms = months(hist, program)
    if month not in ms:
        raise KeyError(f"No data for {month}")
    i = ms.index(month)
    return ms[i - 1] if i > 0 else None


def rank_changes(hist, program, month, prior=None):
    prog = hist["programs"][program]
    prior = prior or _prior_of(hist, program, month)
    if prior is None:
        return []
    now, then = prog[month]["counties"], prog[prior]["counties"]
    rows = []
    for county, cur in now.items():
        if county not in then:
            continue
        was = then[county]
        rows.append({
            "county": county, "now": cur, "then": was,
            "change": cur - was,
            "pct": ((cur - was) / was * 100) if was else 0.0,
        })
    rows.sort(key=lambda r: (r["change"], r["pct"]))
    return rows


def baseline_change(hist, program, month, baseline_month):
    prog = hist["programs"][program]
    now, base = prog[month]["total"], prog[baseline_month]["total"]
    return {
        "month": month, "baseline": baseline_month,
        "now": now, "then": base, "change": now - base,
        "pct": ((now - base) / base * 100) if base else 0.0,
    }
