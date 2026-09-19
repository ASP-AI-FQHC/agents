"""Append-only enrollment history keyed by program and month."""
import json
import os
import re

_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def to_key(period):
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", (period or "").strip())
    if not m:
        raise ValueError(f"Unparseable period: {period!r}")
    name = m.group(1).lower()
    if name not in _MONTHS:
        raise ValueError(f"Unknown month: {period!r}")
    return f"{m.group(2)}-{_MONTHS.index(name) + 1:02d}"


def load(path):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"programs": {}}


def save(hist, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(hist, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def record(hist, program, period, counties, total, source_url):
    key = to_key(period)
    prog = hist["programs"].setdefault(program, {})
    if key in prog:
        return False
    prog[key] = {"counties": counties, "total": total, "source": source_url}
    return True


def months(hist, program):
    return sorted(hist["programs"].get(program, {}).keys())


def series(hist, program, county):
    prog = hist["programs"].get(program, {})
    return [(k, prog[k]["counties"][county]) for k in sorted(prog)
            if county in prog[k]["counties"]]


# --- Known-bad source registry ----------------------------------------------
# Some archived months cannot be parsed reliably (layout variants in 2021-2022).
# They are retried each run but reported only once: a problem list that repeats
# the same six failures every week trains the reader to ignore the section.

def known_problems(hist):
    return hist.setdefault("known_problems", {})


def note_problem(hist, key, detail, run_date):
    """Record a problem. Returns True if it is new (worth reporting)."""
    kp = known_problems(hist)
    if key in kp:
        kp[key]["last_seen"] = run_date
        return False
    kp[key] = {"detail": detail, "first_seen": run_date, "last_seen": run_date}
    return True


def clear_problem(hist, key):
    known_problems(hist).pop(key, None)
