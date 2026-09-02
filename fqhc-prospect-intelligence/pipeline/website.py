"""Key personnel and board members read from the organization's own website.

Form 990 Part VII is the authoritative source for this, and it is what
:mod:`pipeline.irs` reads. But it only helps for organizations whose filing is
in the IRS bulk download you happen to have, and even then it describes a tax
year that closed 12-24 months ago. Health centers publish the same information,
more currently, on their own "Leadership", "Board of Directors" and "Our Team"
pages -- public, unauthenticated, and linked from the home page HRSA already
gives us.

What this module is careful about, because the data is weaker than a filing:

* **It is stored separately.** Website findings go in ``website_people``, never
  in the Part VII ``people`` table, and the UI labels them as coming from the
  website with a link to the exact page. A filing and a guess never blend.
* **It requires a title, not just a name.** A capitalised phrase alone is not
  evidence of anything -- "Patient Portal" reads like a name. A row is only
  recorded when a plausible person name sits next to a phrase that names a role.
* **It only records what the page states.** An email is captured only where the
  organization printed it beside that person, whether linked or as plain text.
  Nothing is constructed from a name and a domain, and a shared inbox --
  ``info@``, ``reception@``, or any address that lands on more than one person
  -- is dropped rather than passed off as somebody's direct line.
* **It is polite.** robots.txt is honoured, requests are throttled, redirects
  off the organization's own host are not followed, and nothing behind a login
  is touched.

Every crawl is recorded in ``website_crawls`` whether or not it found anything,
so "not looked at yet" stays distinguishable from "looked at, site says
nothing".
"""

from __future__ import annotations

import re
import urllib.robotparser
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    IngestRun,
    Organization,
    Person,
    RunStatus,
    WebsiteCrawl,
    WebsitePerson,
    utcnow,
)
from pipeline import apify
from pipeline.propublica import RateLimiter

ProgressFn = Callable[[str], None]


# ---------------------------------------------------------------------------
# Recognising a name and a title
# ---------------------------------------------------------------------------

# Post-nominals that follow a name. Common enough in a health center that
# ignoring them would drop most of the clinical leadership.
CREDENTIALS = frozenset(
    {
        "md", "do", "rn", "np", "pa", "pac", "dds", "dmd", "phd", "psyd", "pharmd",
        "dnp", "msn", "bsn", "mph", "mba", "mha", "ma", "ms", "msw", "lcsw", "jd",
        "cpa", "fache", "facp", "faafp", "cmpe", "chcio", "rd", "lpn", "cnm", "od",
        "esq", "ii", "iii", "iv", "jr", "sr",
    }
)

# Lowercase particles that legitimately appear inside a surname.
PARTICLES = frozenset({"de", "del", "der", "van", "von", "la", "le", "da", "di", "du", "bin", "al"})

# A role phrase must contain one of these. This is the main precision control:
# it is what separates "Maria Delgado -- Chief Financial Officer" from the two
# capitalised words in a navigation menu.
#
# Matched at a word boundary, never as a bare substring, and the abbreviations
# are bounded at both ends. Plain containment is not good enough here: "cto" is
# inside "Hector" and "coo" is inside "Cooper", so a substring test would
# classify real people's names as job titles and silently drop them.
_TITLE_PATTERN = re.compile(
    r"\b(?:chief|officer|president|executive|director|board|chair|trustee"
    r"|treasurer|secretary|administrator|principal|founder|controller"
    r"|superintendent|governance|manager)"
    r"|\b(?:ceo|cfo|coo|cio|cmo|cno|cto|cco|chro|vp|svp|evp)\b"
    r"|\bhead of\b",
    re.IGNORECASE,
)

# Words that appear in the capitalised phrases a health center website is full
# of -- menu labels, service lines, section headings -- and never in a person's
# name. One of these anywhere in a candidate disqualifies it.
NON_PERSON_WORDS = frozenset(
    {
        "about", "appointment", "appointments", "behavioral", "billing", "care",
        "careers", "center", "centers", "centre", "clinic", "clinics",
        "community", "contact", "dental", "department", "donate", "espanol",
        "español", "events", "family", "federally", "foundation", "group",
        "health", "healthcare", "home", "hospital", "hours", "inc", "insurance",
        "llc", "locations", "management", "medical", "medicine", "network",
        "news", "nursing", "partners", "patient", "patients", "pediatrics",
        "pharmacy", "portal", "practice", "program", "programs", "provider",
        "providers", "qualified", "resources", "senior", "service", "services",
        "solutions", "staff", "systems", "team", "university", "volunteer",
        "welcome", "wellness",
        # Abstract nouns that head an "About us" block. "Our Mission" was read
        # as the chief executive of a health center, which is the exact failure
        # this list exists to prevent.
        "mission", "vision", "values", "history", "story", "overview",
        "testimonials", "impact", "philosophy", "commitment", "purpose",
    }
)

# A name never begins with one of these. Checked against the first word only,
# so a surname that happens to be an ordinary word elsewhere in the line is
# unaffected.
SECTION_OPENERS = frozenset(
    {
        "our", "your", "my", "the", "we", "us", "why", "what", "who", "how",
        "when", "where", "join", "meet", "learn", "read", "see", "view",
        "explore", "discover", "find", "get", "make", "help", "support",
    }
)

# Phrases that mark a line as a heading or a piece of prose about the board
# rather than a person or that person's title.
SECTION_PHRASES: tuple[str, ...] = (
    "board of directors", "board of trustees", "our board", "our team",
    "our leadership", "leadership team", "executive team", "management team",
    "senior leadership", "meet the", "governing board", "board members",
    "staff directory", "the board", "corporate compliance", "table of contents",
)

# Shared mailboxes. Attaching one of these to a named person would turn a
# reception address into "the CFO's email", which is worse than no address at
# all -- it reads as a direct line and is not one.
SHARED_MAILBOXES = frozenset(
    {
        "info", "contact", "contactus", "admin", "administration", "hello",
        "office", "reception", "frontdesk", "appointments", "appointment",
        "billing", "accounts", "hr", "humanresources", "careers", "jobs",
        "recruiting", "media", "press", "marketing", "support", "help",
        "helpdesk", "enquiries", "inquiries", "feedback", "webmaster",
        "noreply", "no-reply", "donotreply", "mail", "email", "general",
        "referrals", "records", "compliance", "privacy", "scheduling",
        "patientservices", "customerservice",
    }
)


def is_shared_mailbox(address: str) -> bool:
    local = address.split("@", 1)[0].lower()
    return re.sub(r"[^a-z]", "", local) in SHARED_MAILBOXES


_NAME_SPLIT = re.compile(r"\s*[,–—|/]\s*|\s+[-–—]\s+")
_WHITESPACE = re.compile(r"\s+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _tidy(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip(" \t\r\n *:;")


def _is_credential(token: str) -> bool:
    return re.sub(r"[^a-z]", "", token.lower()) in CREDENTIALS


def looks_like_name(text: str) -> bool:
    """Whether a short line reads as a person's name.

    Conservative on purpose. Two to five capitalised words, no digits, no role
    words, no section headings -- everything else is rejected, because a false
    name is worse than a missing one in a database that feeds real outreach.
    """
    text = _tidy(text)
    if not text or len(text) > 60 or any(ch.isdigit() for ch in text):
        return False

    lowered = text.lower()
    if any(phrase in lowered for phrase in SECTION_PHRASES):
        return False
    if _TITLE_PATTERN.search(lowered):
        return False
    if "@" in text or "http" in lowered:
        return False
    if any(
        re.sub(r"[^a-zà-ÿ]", "", word) in NON_PERSON_WORDS for word in lowered.split()
    ):
        return False

    words = lowered.split()
    if words and re.sub(r"[^a-zà-ÿ]", "", words[0]) in SECTION_OPENERS:
        return False

    tokens = [token for token in text.split() if token]
    # Drop trailing credentials: "Ana Ruiz MD" is a two-word name.
    while tokens and _is_credential(tokens[-1]):
        tokens.pop()
    if not 2 <= len(tokens) <= 5:
        return False

    # A one-letter surname is a truncated listing -- "Shannon C." off a
    # testimonial, not a chief executive. Somebody who cannot be addressed
    # properly is not a usable contact, so the row is dropped rather than
    # carried with a stub for a name.
    if len(re.sub(r"[^A-Za-zà-ÿ]", "", tokens[-1])) < 2:
        return False

    for token in tokens:
        stripped = re.sub(r"[^A-Za-z'’.-]", "", token)
        if not stripped:
            return False
        if stripped.lower() in PARTICLES:
            continue
        if not stripped[0].isupper():
            return False
        # Reject SHOUTED navigation ("HOME", "CONTACT US") only when the whole
        # line is upper case and long enough to be a menu label, not initials.
        if stripped.isupper() and len(stripped) > 4 and text.isupper():
            return False
    return True


def looks_like_title(text: str) -> bool:
    """Whether a short line reads as a job title or board role."""
    text = _tidy(text)
    if not text or len(text) > 100:
        return False
    lowered = text.lower()
    if any(phrase == lowered for phrase in SECTION_PHRASES):
        return False
    if len(text.split()) > 12:
        return False
    return bool(_TITLE_PATTERN.search(lowered))


def split_name_and_title(line: str) -> tuple[str, str] | None:
    """Split "Jane Okafor, Chief Executive Officer" into its two halves.

    Handles the comma, dash, pipe and slash separators that leadership pages
    use interchangeably. Returns None when the line is not of that shape.
    """
    parts = [part for part in _NAME_SPLIT.split(line) if _tidy(part)]
    if len(parts) < 2:
        return None

    # Credentials that were split off as their own part belong to the name.
    name_parts = [parts[0]]
    index = 1
    while index < len(parts) and _is_credential(parts[index]):
        name_parts.append(parts[index])
        index += 1
    if index >= len(parts):
        return None

    name = _tidy(", ".join(name_parts))
    title = _tidy(", ".join(parts[index:]))
    if looks_like_name(name) and looks_like_title(title):
        return name, title
    return None


# ---------------------------------------------------------------------------
# HTML to text runs
# ---------------------------------------------------------------------------

_SKIP_TAGS = frozenset({"script", "style", "noscript", "head", "svg", "template"})
# Tags that end a visual line. Anything between two of these is one text run,
# which is how a name and a title end up as separate lines to pair up.
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "li", "ul", "ol", "td", "th", "tr", "table", "br", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
        "footer", "nav", "aside", "figure", "figcaption", "dd", "dt", "dl",
        "blockquote", "main", "form", "option",
    }
)


class _PageParser(HTMLParser):
    """Collects visible text runs, links, and mailto addresses in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.runs: list[str] = []
        self.links: list[tuple[str, str]] = []   # (href, link text)
        self.emails: list[tuple[int, str]] = []  # (index into runs, address)
        self.title: str | None = None
        self._skip_depth = 0
        self._buffer: list[str] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._in_title = False

    # -- buffering ---------------------------------------------------------
    def _flush(self) -> None:
        text = _tidy("".join(self._buffer))
        self._buffer.clear()
        if text:
            self.runs.append(text)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            if tag == "head":
                self._in_title = False
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag == "title":
            self._in_title = True
        if tag == "a":
            attributes = dict(attrs)
            self._href = attributes.get("href") or None
            self._link_text = []
            if self._href and self._href.lower().startswith("mailto:"):
                address = self._href[7:].split("?", 1)[0].strip()
                if _EMAIL.fullmatch(address):
                    # Attach to the run that is about to be flushed, or the last
                    # one already flushed if this link sits alone.
                    self.emails.append((len(self.runs), address))

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS and not self._skip_depth:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        if tag == "a":
            if self._href:
                self.links.append((self._href, _tidy("".join(self._link_text))))
            self._href = None
            self._link_text = []
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title and self.title is None:
            candidate = _tidy(data)
            if candidate:
                self.title = candidate
        self._buffer.append(data)
        if self._href is not None:
            self._link_text.append(data)

    def close(self) -> None:  # pragma: no cover - trivial
        super().close()
        self._flush()


def parse_page(html: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    return parser


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebsitePersonRecord:
    name: str
    title: str
    email: str | None = None


def extract_people(html: str, *, limit: int = 60) -> list[WebsitePersonRecord]:
    """Pull name/title pairs out of one page of HTML.

    Two shapes cover almost every leadership page in the wild: the pair on one
    line ("Jane Okafor, CEO"), and the pair on consecutive lines, which is what
    a card grid produces. Both are required to carry a role phrase, so prose and
    navigation are left behind.
    """
    page = parse_page(html)
    runs = page.runs
    emails_by_run: dict[int, str] = {}
    for index, address in page.emails:
        emails_by_run.setdefault(index, address)
    # Plenty of health centers print the address as plain text instead of
    # linking it. Still published by the organization, still verbatim -- the
    # only difference is the markup, so there is no reason to ignore it.
    for index, run in enumerate(runs):
        match = _EMAIL.search(run)
        if match:
            emails_by_run.setdefault(index, match.group(0))

    found: list[WebsitePersonRecord] = []
    seen: set[str] = set()
    used: set[int] = set()

    def add(name: str, title: str, consumed: set[int], window: range) -> None:
        key = re.sub(r"[^a-z]", "", name.lower())
        if not key or key in seen:
            return
        seen.add(key)
        # A published address usually sits a line or two below the title, so the
        # search window is wider than the lines the person themselves occupies.
        email = next(
            (
                emails_by_run[i]
                for i in window
                if i in emails_by_run and not is_shared_mailbox(emails_by_run[i])
            ),
            None,
        )
        found.append(WebsitePersonRecord(name=name, title=title, email=email))
        used.update(consumed)

    for index, run in enumerate(runs):
        if len(found) >= limit:
            break
        if index in used:
            continue

        combined = split_name_and_title(run)
        if combined is not None:
            # Only this line is consumed: the next one belongs to the next
            # person, not to this one.
            add(combined[0], combined[1], {index}, range(index, index + 2))
            continue

        # Name on one line, title on the next (or the one after, when a photo
        # caption or credential line sits between them).
        if not looks_like_name(run):
            continue
        for offset in (1, 2):
            neighbour = index + offset
            if neighbour >= len(runs) or neighbour in used:
                continue
            # A neighbour that is itself a complete "Name, Title" line belongs
            # to somebody else; claiming it as this line's title would invent a
            # person and swallow a real one.
            if split_name_and_title(runs[neighbour]) is not None:
                break
            if looks_like_title(runs[neighbour]):
                add(
                    run,
                    _tidy(runs[neighbour]),
                    {index, neighbour},
                    range(index, neighbour + 2),
                )
                break

    # An address that landed on more than one person is a shared inbox that
    # happened not to be named like one. It belongs to none of them.
    counts = Counter(person.email for person in found if person.email)
    duplicated = {address for address, count in counts.items() if count > 1}
    if duplicated:
        found = [
            replace(person, email=None) if person.email in duplicated else person
            for person in found
        ]

    return found[:limit]


# ---------------------------------------------------------------------------
# Finding the leadership page
# ---------------------------------------------------------------------------

# Ordered best-first: a page called "Board of Directors" beats a generic
# "About Us" when only a few fetches are allowed.
LINK_HINTS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (100, ("board-of-directors", "boardofdirectors", "board of directors")),
    (95, ("board-of-trustees", "board of trustees", "governing-board", "governance")),
    (90, ("leadership", "our-leadership", "senior-leadership")),
    (85, ("executive-team", "management-team", "our-team", "meet-our-team", "our team")),
    (80, ("board", "trustees", "directors")),
    (70, ("administration", "executive", "management")),
    (60, ("our-staff", "staff", "providers", "meet-the-team")),
    (40, ("about-us", "about", "who-we-are", "whoweare")),
)

# Never worth fetching, and cheap to rule out.
_SKIP_LINK = re.compile(
    r"\.(pdf|jpe?g|png|gif|svg|zip|docx?|xlsx?|mp4|mp3)($|\?)", re.IGNORECASE
)


def same_site(base: str, candidate: str) -> bool:
    """Whether two URLs share a registrable host, ignoring a leading www."""

    def host(url: str) -> str:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc

    return bool(host(base)) and host(base) == host(candidate)


def rank_links(base_url: str, links: list[tuple[str, str]]) -> list[str]:
    """Candidate leadership-page URLs on the same site, best first."""
    scored: dict[str, int] = {}
    for href, text in links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href).split("#", 1)[0].rstrip("/")
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if _SKIP_LINK.search(absolute) or not same_site(base_url, absolute):
            continue
        if absolute.rstrip("/") == base_url.rstrip("/"):
            continue

        haystack = f"{absolute.lower()} {text.lower()}"
        best = 0
        for weight, hints in LINK_HINTS:
            if any(hint in haystack for hint in hints):
                best = max(best, weight)
        if best:
            scored[absolute] = max(scored.get(absolute, 0), best)

    return [url for url, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))]


def normalize_website(value: str | None) -> str | None:
    """HRSA's web address column into a fetchable URL, or None."""
    text = (value or "").strip()
    if not text or text.lower() in {"n/a", "na", "none", "not available"}:
        return None
    if "." not in text:
        return None
    if not text.lower().startswith(("http://", "https://")):
        text = f"https://{text}"
    parsed = urlparse(text)
    if not parsed.netloc or " " in parsed.netloc:
        return None
    return text.rstrip("/")


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


class SiteFetcher:
    """Throttled, robots-aware fetcher scoped to one run."""

    def __init__(
        self,
        config: Config,
        *,
        client: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.config = config
        self.settings = config.website
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=self.settings.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.settings.user_agent},
        )
        self.limiter = limiter or RateLimiter(self.settings.requests_per_second)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def __enter__(self) -> "SiteFetcher":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    # -- robots ------------------------------------------------------------
    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        parser: urllib.robotparser.RobotFileParser | None = None
        try:
            self.limiter.wait()
            response = self.client.get(f"{origin}/robots.txt")
            if response.status_code == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
        except Exception:
            # A site with no reachable robots.txt is treated as unrestricted,
            # which is what the standard says and what every crawler does.
            parser = None
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.settings.user_agent, url)
        except Exception:
            return True

    # -- pages -------------------------------------------------------------
    def fetch(self, url: str) -> str | None:
        """HTML for one URL, or None if it is disallowed, missing or not HTML."""
        if not self.allowed(url):
            return None
        try:
            self.limiter.wait()
            response = self.client.get(url)
        except Exception:
            return None
        if response.status_code != 200:
            return None
        content_type = response.headers.get("content-type", "")
        if content_type and "html" not in content_type.lower():
            return None
        return response.text


@dataclass
class SiteResult:
    """What one organization's website yielded."""

    url: str | None
    people: list[WebsitePersonRecord] = field(default_factory=list)
    pages_fetched: int = 0
    source_urls: dict[str, str] = field(default_factory=dict)
    outcome: str = "ok"


def collect_from_site(
    fetcher: SiteFetcher,
    website: str | None,
    *,
    extra_urls: list[str] | None = None,
) -> SiteResult:
    """Read the home page, follow the most promising leadership links, extract."""
    url = normalize_website(website)
    if url is None:
        return SiteResult(url=None, outcome="no website on file")

    if not fetcher.allowed(url):
        return SiteResult(url=url, outcome="blocked by robots.txt")

    homepage = fetcher.fetch(url)
    if homepage is None:
        return SiteResult(url=url, outcome="home page unreachable")

    result = SiteResult(url=url, pages_fetched=1)
    limit = fetcher.settings.max_people_per_org

    def absorb(page_url: str, html: str) -> None:
        for record in extract_people(html, limit=limit):
            key = re.sub(r"[^a-z]", "", record.name.lower())
            if key in result.source_urls:
                continue
            result.source_urls[key] = page_url
            result.people.append(record)

    candidates = rank_links(url, parse_page(homepage).links)

    # URLs a search engine found that the home page does not link to. They go
    # after the site's own links, never instead of them, and they are fetched
    # through this same fetcher -- robots.txt, rate limit and extraction rules
    # all unchanged. Search located the page; it did not read it.
    for extra in extra_urls or ():
        cleaned = extra.split("#", 1)[0].rstrip("/")
        if same_site(url, cleaned) and cleaned not in candidates:
            candidates.append(cleaned)

    for candidate in candidates[: fetcher.settings.max_pages_per_org]:
        html = fetcher.fetch(candidate)
        if html is None:
            continue
        result.pages_fetched += 1
        absorb(candidate, html)

    # The home page itself is read last and only if nothing better turned up:
    # its "meet our providers" strip is the weakest version of this data.
    if not result.people:
        absorb(url, homepage)

    if not result.people:
        result.outcome = (
            "no leadership page found" if not candidates else "no names on leadership pages"
        )
    elif extra_urls and any(
        page in (extra_urls or ()) or page.rstrip("/") in [
            u.split("#", 1)[0].rstrip("/") for u in extra_urls
        ]
        for page in result.source_urls.values()
    ):
        result.outcome = "found via search"
    return result


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


@dataclass
class WebsiteResult:
    eligible: int = 0
    crawled: int = 0
    skipped_recent: int = 0
    without_website: int = 0
    # Apify search fallback, when it is switched on.
    searched: int = 0
    found_via_search: int = 0
    blocked_results: int = 0
    people_written: int = 0
    organizations_with_people: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def status(self) -> RunStatus:
        return RunStatus.SUCCESS


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything stored is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _eligible_organizations(session: Session, config: Config) -> list[Organization]:
    """Organizations to consider, in the same footprint the API stages use."""
    statement = select(Organization).order_by(Organization.name)
    footprint = config.api_states
    if footprint:
        statement = statement.where(Organization.state.in_(footprint))
    return list(session.scalars(statement).all())


def _has_filing_people(session: Session, organization: Organization) -> bool:
    """Whether Form 990 Part VII already named people for this organization."""
    ein = organization.ein
    if not ein:
        return False
    return (
        session.scalar(select(Person.id).where(Person.ein == ein).limit(1)) is not None
    )


def enrich_websites(
    session: Session,
    config: Config,
    *,
    fetcher: SiteFetcher | None = None,
    limit: int | None = None,
    force: bool = False,
    on_progress: ProgressFn | None = None,
    search_client_factory: Callable[[], Any] | None = None,
) -> WebsiteResult:
    """Populate ``website_people`` from organization leadership pages."""
    report = on_progress or (lambda _message: None)
    result = WebsiteResult()

    run = IngestRun(stage="website", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    if not config.website.enabled:
        report("Website lookup is disabled (website.enabled in config.yaml)")
        run.status = RunStatus.SUCCESS
        run.finished_at = utcnow()
        run.message = "disabled in config"
        session.commit()
        return result

    owns_fetcher = fetcher is None
    fetcher = fetcher or SiteFetcher(config)
    cutoff = utcnow() - timedelta(days=config.website.refresh_after_days)

    # The search fallback is opt-in and costs money, so it announces itself --
    # both when it is on and when it is on but unusable for want of a token.
    apify_token = None
    search_client = None
    if config.website.use_apify_search:
        apify_token = apify.token()
        if apify_token:
            search_client = search_client_factory or (lambda: httpx.Client())
            search_client = search_client()
            report(
                "Apify search is on: organizations whose own site yields no "
                "leadership page will have one looked up, up to "
                f"{config.website.apify_max_searches:,} of them"
            )
        else:
            result.messages.append(
                "website.use_apify_search is on but APIFY_TOKEN is not set in "
                "the environment, so no search was run. Export the token in "
                "the shell you run the pipeline from; it is deliberately not "
                "read from config.yaml, which is committed."
            )
            report(result.messages[-1])

    try:
        organizations = _eligible_organizations(session, config)
        report(f"{len(organizations):,} organizations in the footprint")

        for organization in organizations:
            if limit is not None and result.crawled >= limit:
                break

            if config.website.only_when_missing and _has_filing_people(
                session, organization
            ):
                continue
            result.eligible += 1

            previous = session.scalar(
                select(WebsiteCrawl).where(
                    WebsiteCrawl.organization_id == organization.id
                )
            )
            if not force and previous is not None and _as_utc(previous.fetched_at) > cutoff:
                result.skipped_recent += 1
                continue

            site = collect_from_site(fetcher, organization.website)

            # Only when the organization's own site yielded nothing, and only
            # while there is budget left. A search that confirms what we
            # already have is money spent for no new fact.
            if (
                apify_token
                and not site.people
                and site.url is not None
                and site.outcome != "blocked by robots.txt"
                and result.searched < config.website.apify_max_searches
            ):
                result.searched += 1
                urls, blocked = apify.find_leadership_pages(
                    search_client,
                    site.url,
                    organization.name,
                    api_token=apify_token,
                    results_per_query=config.website.apify_results_per_query,
                    timeout_seconds=config.website.apify_timeout_seconds,
                )
                result.blocked_results += blocked
                if urls:
                    site = collect_from_site(
                        fetcher, organization.website, extra_urls=urls
                    )
                    if site.people:
                        result.found_via_search += 1

            if site.url is None:
                result.without_website += 1
            else:
                result.crawled += 1

            # Replace this organization's rows wholesale: the site is the unit
            # of truth and a re-read should not leave half of a previous one.
            for row in session.scalars(
                select(WebsitePerson).where(
                    WebsitePerson.organization_id == organization.id
                )
            ).all():
                session.delete(row)
            session.flush()

            for record in site.people:
                key = re.sub(r"[^a-z]", "", record.name.lower())
                session.add(
                    WebsitePerson(
                        organization_id=organization.id,
                        name=record.name,
                        title=record.title,
                        email=record.email,
                        source_url=site.source_urls.get(key, site.url or ""),
                    )
                )
                result.people_written += 1
            if site.people:
                result.organizations_with_people += 1

            crawl = previous or WebsiteCrawl(organization_id=organization.id)
            crawl.url = site.url
            crawl.pages_fetched = site.pages_fetched
            crawl.people_found = len(site.people)
            crawl.outcome = site.outcome
            crawl.fetched_at = utcnow()
            session.add(crawl)
            session.commit()

        session.commit()
    except Exception as exc:
        session.rollback()
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.message = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise
    finally:
        if owns_fetcher:
            fetcher.close()

    if result.searched:
        report(
            f"Searched for a leadership page at {result.searched:,} organizations "
            f"and found one at {result.found_via_search:,}"
            + (
                f"; {result.blocked_results:,} results were dropped as LinkedIn "
                "or contact-broker sites"
                if result.blocked_results
                else ""
            )
        )

    if result.without_website:
        result.messages.append(
            f"{result.without_website:,} organizations have no web address in "
            "the HRSA data"
        )
    if result.skipped_recent:
        result.messages.append(
            f"{result.skipped_recent:,} already read within "
            f"{config.website.refresh_after_days} days"
        )

    report(
        f"Read {result.crawled:,} websites; found {result.people_written:,} "
        f"named people at {result.organizations_with_people:,} organizations"
    )

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.eligible
    run.records_written = result.people_written
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result
