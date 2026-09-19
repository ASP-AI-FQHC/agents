"""Fingerprint watched pages by their link sets.

Raw-HTML hashing produces false positives from banners and timestamps.
Link sets change only when HFS actually posts or removes something.
"""
import re

_LINK = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def fingerprint(html):
    out = {}
    for href, text in _LINK.findall(html):
        label = " ".join(_TAGS.sub("", text).replace("​", "").split())
        if label:
            out[label] = href
    return out


def diff_fingerprints(old, new):
    return {
        "added": {k: v for k, v in new.items() if k not in old},
        "removed": {k: v for k, v in old.items() if k not in new},
    }
