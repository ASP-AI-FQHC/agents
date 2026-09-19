from tracker.validate import validate


def good():
    return {"counties": {f"C{i}": 100 for i in range(102)}, "total": 10200, "conflicts": []}


def test_clean_section_has_no_problems():
    assert validate(good()) == []


def test_conflicts_are_fatal():
    s = good(); s["conflicts"] = ["Cook"]
    assert any("Cook" in p for p in validate(s))


def test_sum_must_match_stated_total_within_tolerance():
    s = good(); s["total"] = 20000
    assert any("total" in p.lower() for p in validate(s))


def test_small_variance_is_tolerated():
    s = good(); s["total"] = 10230          # 0.29%, HIPAA suppression artifact
    assert validate(s) == []


def test_short_county_count_fatal_without_a_total_to_reconcile():
    s = good(); s["counties"] = {f"C{i}": 100 for i in range(40)}; s["total"] = None
    assert any("102" in p for p in validate(s))


def test_short_county_count_accepted_when_total_reconciles():
    """Some months omit fully-suppressed counties; if rows sum to the stated
    total the month is internally consistent and must not be discarded."""
    s = good(); s["counties"] = {f"C{i}": 100 for i in range(100)}; s["total"] = 10000
    assert validate(s) == []


def test_empty_parse_reports_and_stops():
    assert validate({"counties": {}, "total": None, "conflicts": []}) == ["No county rows parsed"]
