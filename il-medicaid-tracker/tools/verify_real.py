import glob, sys
from tracker.extract import extract_text
from tracker.parse import parse_text

bad = 0
rows = []
for f in sorted(glob.glob('pdfs/*.pdf')):
    try:
        secs = parse_text(extract_text(f))
    except Exception as e:
        print("ERR", f, e); bad += 1; continue
    for (prog, period), sec in secs.items():
        if prog != 'healthchoice':
            continue
        n, ssum, tot = len(sec['counties']), sum(sec['counties'].values()), sec['total']
        ok = tot is not None and abs(ssum - tot) <= max(5, tot * 0.005)
        rows.append((period, n, ssum, tot, ok, bool(sec['conflicts'])))
        if sec['conflicts']:
            bad += 1
print(f"{'period':18s}{'cty':>5}{'sum':>12}{'stated':>12}  ok")
for period, n, ssum, tot, ok, conf in rows:
    print(f"{period:18s}{n:5d}{ssum:12,}{(tot if tot else 0):12,}  {'OK' if ok else ('CONFLICT' if conf else 'CHK')}")
print(f"\nsections={len(rows)} conflicts/errors={bad}")
