"""Refuse untrustworthy parses.

A silently wrong number is worse than no number: a county appearing to drop
to zero because of a layout change would send the reader down a false trail.
"""
TOLERANCE = 0.005


def validate(section, expect_full_state=True):
    problems = []
    counties = section.get("counties") or {}
    total = section.get("total")
    conflicts = section.get("conflicts") or []

    if conflicts:
        problems.append(
            f"Duplicate county rows indicate mis-attribution: "
            f"{', '.join(sorted(set(conflicts))[:5])}"
        )
    if not counties:
        problems.append("No county rows parsed")
        return problems

    if total is not None:
        ssum = sum(counties.values())
        allowed = max(5, total * TOLERANCE)
        if abs(ssum - total) > allowed:
            problems.append(
                f"Row sum {ssum:,} does not match stated total {total:,} "
                f"(difference {abs(ssum - total):,}, allowed {allowed:,.0f})"
            )
    # A short county list is only fatal when there is no stated total to
    # reconcile against. Some months genuinely omit counties whose enrollment
    # is fully suppressed under HIPAA; if the rows still sum to the stated
    # total, the data is internally consistent and trustworthy.
    if expect_full_state and len(counties) < 102 and total is None:
        problems.append(
            f"Only {len(counties)} counties parsed, expected 102, "
            f"and no stated total to reconcile against"
        )
    return problems
