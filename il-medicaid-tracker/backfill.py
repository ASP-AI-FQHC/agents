#!/usr/bin/env python3
"""One-time backfill of every discoverable archived month.

Run once to establish the pre-cliff baseline. Safe to re-run: months already
recorded are skipped.
"""
import json
import sys
import tempfile

from tracker import discover, extract, history, parse, validate
from tracker.net import fetch, fetch_text


def ingest_pdf(url, text_label, hist):
    """Returns (added_count, problems)."""
    added, problems = 0, []
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(fetch(url))
        tmp.flush()
        sections = parse.parse_text(extract.extract_text(tmp.name))
    if not sections:
        return 0, [f"{text_label}: no enrollment sections found"]
    for (prog, period), sec in sections.items():
        full = len(sec["counties"]) > 50
        probs = validate.validate(sec, expect_full_state=full)
        if probs:
            problems.append(f"{period} [{prog}]: {probs[0]}")
            continue
        if history.record(hist, prog, period, sec["counties"], sec["total"], url):
            added += 1
    return added, problems


def main(state_path="state.json"):
    cfg = json.load(open("sources.json"))
    hist = history.load(state_path)
    links = []
    for idx in cfg["enrollment_index"]:
        links += discover.find_enrollment_links(fetch_text(idx))

    seen, added, problems = set(), 0, []
    for url, label in links:
        if url in seen:
            continue
        seen.add(url)
        try:
            n, probs = ingest_pdf(url, label, hist)
            added += n
            problems += probs
        except Exception as e:                   # noqa: BLE001
            problems.append(f"{label}: {type(e).__name__}: {e}")

    history.save(hist, state_path)
    print(f"Discovered {len(seen)} PDFs. Added {added} program-months. "
          f"Problems: {len(problems)}.")
    for p in problems:
        print("  SKIP", p)
    for prog in sorted(hist["programs"]):
        ms = history.months(hist, prog)
        if ms:
            print(f"  {prog}: {len(ms)} months, {ms[0]} -> {ms[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
