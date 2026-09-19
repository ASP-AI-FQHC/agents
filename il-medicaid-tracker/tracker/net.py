"""Single place for outbound fetches, so egress failures report identically."""
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; ASP-IL-Medicaid-Tracker/1.0)"}


class FetchFailed(RuntimeError):
    pass


def fetch(url, timeout=60):
    try:
        req = urllib.request.Request(url, headers=UA)
        return urllib.request.urlopen(req, timeout=timeout).read()
    except Exception as e:                       # noqa: BLE001 - report, never mask
        raise FetchFailed(f"{url}: {type(e).__name__}: {e}") from e


def fetch_text(url, timeout=60):
    return fetch(url, timeout).decode("utf-8", "replace")
