import urllib.request
from tracker.discover import find_enrollment_links, period_from_text

UA = {"User-Agent": "Mozilla/5.0 (compatible; ASP-IL-Medicaid-Tracker/1.0)"}
IDX = ["https://hfs.illinois.gov/medicalproviders/cc/totalccenrollmentforallprograms.html",
       "https://hfs.illinois.gov/medicalproviders/cc/archivedtotalcareenrollmentforallprograms.html"]

found, undated = {}, []
for u in IDX:
    html = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read().decode("utf-8", "replace")
    for url, text in find_enrollment_links(html):
        p = period_from_text(text)
        if p:
            found.setdefault(p, url)
        else:
            undated.append(text)
print("distinct months discovered:", len(found))
ks = sorted(found)
print("range:", ks[0], "->", ks[-1])
print("undated anchors:", len(undated), undated[:3])
years = {}
for k in ks:
    years[k[:4]] = years.get(k[:4], 0) + 1
print("per year:", dict(sorted(years.items())))
