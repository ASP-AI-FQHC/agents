#!/usr/bin/env python3
"""One weekly cycle: ingest any new month, diff watched pages, print a report.

Prints the literal token NO_UPDATE and exits 0 when there is nothing to say,
so the routine can stay silent rather than mailing noise.

When there is a report, the same content is also written to report.html as the
rich alternative for the email. The printed text stays the source of truth.

Exit codes: 0 normal (including NO_UPDATE), 1 unexpected failure.
"""
import datetime
import json
import os
import sys

from backfill import ingest_pdf
from tracker import deltas, discover, history, report, watch
from tracker.net import fetch_text, FetchFailed

STATE = "state.json"
FPRINTS = "fingerprints.json"
SOURCES = "sources.json"
HTML_OUT = "report.html"
PROGRAM = "healthchoice"
BASELINE_MONTH = "2026-01"   # pre-cliff reference


def ingest_new_months(cfg, hist, problems, today):
    """Fetch only months not already recorded. Returns newly added month keys.

    Parse failures are recorded once. Re-reporting the same unparseable 2022
    months every week would bury a genuinely new failure in familiar noise.
    """
    known = set(history.months(hist, PROGRAM))
    links, seen, new_keys = [], set(), []
    for idx in cfg["enrollment_index"]:
        try:
            links += discover.find_enrollment_links(fetch_text(idx))
        except FetchFailed as e:
            problems.append(str(e))
    for url, label in links:
        if url in seen:
            continue
        seen.add(url)
        hint = discover.period_from_text(label) or discover.period_hint(url)
        if hint and f"{hint[:4]}-{hint[4:]}" in known:
            continue                     # already have this month
        try:
            before = set(history.months(hist, PROGRAM))
            _, probs = ingest_pdf(url, label, hist)
            for detail in probs:
                if history.note_problem(hist, f"{url}|{detail[:40]}", detail, today):
                    problems.append(detail)
            new_keys += sorted(set(history.months(hist, PROGRAM)) - before)
        except Exception as e:           # noqa: BLE001
            detail = f"{label}: {type(e).__name__}: {e}"
            if history.note_problem(hist, f"{url}|err", detail, today):
                problems.append(detail)
    return new_keys


def run_cliff_watch(cfg, problems):
    """Returns (alerts, fingerprints, first_run)."""
    first_run = not os.path.exists(FPRINTS)
    old = json.load(open(FPRINTS)) if not first_run else {}
    new, alerts = {}, []
    for src in cfg["cliff_watch"]:
        try:
            fp = watch.fingerprint(fetch_text(src["url"]))
        except FetchFailed as e:
            problems.append(str(e))
            new[src["id"]] = old.get(src["id"], {})
            continue
        new[src["id"]] = fp
        if first_run:
            continue                     # establish baseline silently
        for label in watch.diff_fingerprints(old.get(src["id"], {}), fp)["added"]:
            alerts.append(f"[{src['id']}] new: {label}")
    return alerts, new, first_run


def main():
    cfg = json.load(open(SOURCES))
    hist = history.load(STATE)
    problems = []
    today = datetime.date.today().isoformat()
    if os.path.exists(HTML_OUT):
        os.remove(HTML_OUT)              # never let a stale report be sent

    new_months = ingest_new_months(cfg, hist, problems, today)
    alerts, fprints, first_run = run_cliff_watch(cfg, problems)

    history.save(hist, STATE)
    with open(FPRINTS, "w") as f:
        json.dump(fprints, f, indent=2, sort_keys=True)

    if not new_months and not alerts and not problems:
        print("NO_UPDATE")
        return 0

    ms = history.months(hist, PROGRAM)
    if not ms:
        print("NO_DATA — no enrollment history available")
        return 0

    month = sorted(new_months)[-1] if new_months else ms[-1]
    prog = hist["programs"][PROGRAM]
    i = ms.index(month)
    mom = prog[month]["total"] - prog[ms[i - 1]]["total"] if i > 0 else 0
    ranked = deltas.rank_changes(hist, PROGRAM, month)
    base = (deltas.baseline_change(hist, PROGRAM, month, BASELINE_MONTH)
            if BASELINE_MONTH in prog and BASELINE_MONTH != month else None)

    if first_run:
        problems.append("First run: cliff-watch baseline established, "
                        "page changes will be reported from next run onward.")

    args = (month, prog[month]["total"], mom, ranked, alerts, problems)
    print(report.subject(month, mom, alerts))
    print()
    print(report.compose(*args, baseline=base))
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(report.compose_html(*args, baseline=base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
