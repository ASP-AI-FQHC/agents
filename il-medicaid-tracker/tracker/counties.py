"""Canonical Illinois county list.

Match by normalized name, never by column position: MCO columns appear and
disappear across format eras (IlliniCare, BCBS), but county names do not.
"""
import re

COUNTIES = """Adams Alexander Bond Boone Brown Bureau Calhoun Carroll Cass Champaign Christian Clark Clay
Clinton Coles Cook Crawford Cumberland DeKalb DeWitt Douglas DuPage Edgar Edwards Effingham Fayette Ford
Franklin Fulton Gallatin Greene Grundy Hamilton Hancock Hardin Henderson Henry Iroquois Jackson Jasper
Jefferson Jersey JoDaviess Johnson Kane Kankakee Kendall Knox Lake LaSalle Lawrence Lee Livingston Logan
Macon Macoupin Madison Marion Marshall Mason Massac McDonough McHenry McLean Menard Mercer Monroe
Montgomery Morgan Moultrie Ogle Peoria Perry Piatt Pike Pope Pulaski Putnam Randolph Richland RockIsland
Saline Sangamon Schuyler Scott Shelby Stark StClair Stephenson Tazewell Union Vermilion Wabash Warren
Washington Wayne White Whiteside Will Williamson Winnebago Woodford""".split()


def _norm(s):
    return re.sub(r"[^a-z]", "", s.lower())


_CANON = {_norm(c): c for c in COUNTIES}


def canonical(raw):
    return _CANON.get(_norm(raw))


# --- Ligature-damaged name recovery -----------------------------------------
# Older HFS PDFs (2018-2020) embed fonts whose ligatures pdftotext cannot map.
# Observed corruptions: "Chris+an" (Christian), "Effingham" with an ffi
# ligature, "Faye e" (Fayette, "tt" -> space), "De Wi" / "Pia" / "Sco"
# (trailing "tt" dropped entirely). Left unhandled these counties vanish and
# the month fails validation by ~28,000 enrollees.

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
    "+": "ti", "#": "ti",          # this font's "ti" ligature
}


def _expand(raw):
    out = raw
    for bad, good in _LIGATURES.items():
        out = out.replace(bad, good)
    return out


def _is_subsequence(short, long):
    it = iter(long)
    return all(c in it for c in short)


def canonical_fuzzy(raw):
    """Canonical name, recovering ligature-damaged labels.

    Corruption only drops or substitutes characters, so a damaged label is a
    subsequence of its true name. Accept only when exactly one county matches,
    so an ambiguous scrap is left unmatched rather than silently mis-assigned.
    """
    exact = canonical(raw)
    if exact:
        return exact
    probe = _norm(_expand(raw))
    if len(probe) < 3:
        return None
    # Prefix first: dropped ligatures preserve the start of the name, so
    # "Pia" is Piatt, not Peoria (which it also matches as a subsequence).
    pref = [c for c in COUNTIES if _norm(c).startswith(probe)]
    if len(pref) == 1:
        return pref[0]
    if pref:
        return None                 # ambiguous prefix: refuse rather than guess
    hits = [c for c in COUNTIES if _is_subsequence(probe, _norm(c))]
    return hits[0] if len(hits) == 1 else None


# Display names: canonical keys are space-free for robust matching, but the
# report should read the way people write these counties.
_DISPLAY = {
    "DeWitt": "De Witt", "JoDaviess": "Jo Daviess", "LaSalle": "La Salle",
    "RockIsland": "Rock Island", "StClair": "St. Clair",
}


def display(name):
    return _DISPLAY.get(name, name)
