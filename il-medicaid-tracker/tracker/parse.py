"""Parse HFS managed-care enrollment PDFs across format eras.

Verified against 24 months spanning 2018-2026. Design notes:
  - Program renamed MMAI -> FIDE-SNP; both normalize to "dual".
  - Take the LAST number on a row as the county total. Per-MCO columns
    shift between eras; the trailing total does not.
  - "Grand Total" terminates a section.
  - Rows appearing before their header (page-break artifact) buffer and are
    claimed by the next continuation header.
"""
import re

from .counties import canonical_fuzzy as canonical

_HDR = re.compile(r"^(.*?)\s+Enrollments?\s*(?:[—–-]|for)\s*([A-Za-z]+\s+\d{4})", re.I)
_NUM = re.compile(r"\d[\d,]*")
# Split at the first numeric/suppressed cell rather than on column spacing,
# which varies between single- and multi-space alignment across eras.
# The label class admits the ligature corruptions found in 2018-2020 files
# ("Chris+an", "Je\ufb00erson"); tracker.counties.canonical_fuzzy resolves them.
_ROW = re.compile(r"^([A-Za-z][A-Za-z.'\s+#\ufb00-\ufb06-]*?)\s+(?=[\d*])(.*)$")


def _program_of(raw):
    r = raw.lower()
    if "mmai" in r or "fide" in r or "snp" in r:
        return "dual"
    if "choice" in r or "hci" in r:
        return "healthchoice"
    return None


def parse_text(text):
    sections, cur, pending = {}, None, []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _HDR.match(s)
        if m:
            prog = _program_of(m.group(1))
            if prog:
                period = re.sub(r"\s*\(continued\)\s*$", "", m.group(2), flags=re.I).strip()
                cur = (prog, period)
                sections.setdefault(cur, {"counties": {}, "total": None, "conflicts": []})
                for name, val in pending:
                    sections[cur]["counties"][name] = val
                pending = []
                continue
        row = _ROW.match(s)
        if not row:
            continue
        nums = _NUM.findall(row.group(2))
        if not nums:
            continue
        val = int(nums[-1].replace(",", ""))
        name = canonical(row.group(1))
        if cur is None:
            if name:
                pending.append((name, val))
            continue
        sec = sections[cur]
        if name:
            if name in sec["counties"] and sec["counties"][name] != val:
                sec["conflicts"].append(name)
            sec["counties"][name] = val
        elif re.sub(r"[^a-z]", "", row.group(1).lower()) in ("grandtotal", "total"):
            sec["total"] = val
            cur = None
    return sections
