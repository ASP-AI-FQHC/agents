"""HRSA ingestion: build the FQHC universe from data.hrsa.gov downloads.

Two public files feed this stage:

* **Health Center Service Delivery and Look-Alike Sites** -- one row per
  *delivery site*. Deduplicating these up to the grantee organization is the
  core of this module: a health center with 14 clinics is one prospect, not 14.
* **Health Center Program awardee data** -- one row per *awardee organization*,
  carrying the Section 330 award amount used by the grant-dependence score.

Two things make this messy in practice, and both are handled here rather than
left to fail at runtime:

* HRSA renames columns between releases, so headers are resolved through a
  tolerant alias + keyword map instead of being indexed by exact name.
* The download URLs move. A failed download falls back to the cached copy and
  the run is reported as cache-backed, so the UI can label the data honestly.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    GranteeType,
    IngestRun,
    Organization,
    RunStatus,
    Site,
    utcnow,
)
from pipeline.cache import CacheEntry, FileCache
from pipeline.text import (
    clean,
    dedup_key,
    normalize_header,
    normalize_name,
    normalize_state,
    normalize_zip,
    parse_money,
)

ProgressFn = Callable[[str], None]


class SourceUnavailable(RuntimeError):
    """Raised when a source is unreachable and no cached copy exists."""


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """How to find one logical field among whatever headers HRSA shipped.

    ``aliases`` are matched exactly (after normalization). If none hit, the
    first header containing every string in one of the ``contains`` groups --
    and none of the ``exclude`` strings -- wins.
    """

    aliases: tuple[str, ...] = ()
    contains: tuple[tuple[str, ...], ...] = ()
    exclude: tuple[str, ...] = ()


# Site file. Order matters: site-scoped fields are resolved before the
# organization-scoped fallbacks so "Site State" is not claimed by "state".
SITE_FIELDS: dict[str, FieldSpec] = {
    "hrsa_id": FieldSpec(
        aliases=(
            "BHCMIS Organization Identification Number",
            "BHCMISID",
            "BHCMIS ID",
            "Health Center Organization Identification Number",
            "Organization ID",
        ),
        contains=(("bhcmis",), ("organization", "identification")),
    ),
    "grant_number": FieldSpec(
        aliases=("Grant Number", "Health Center Grant Number", "BPHC Assigned Number"),
        contains=(("grant", "number"),),
    ),
    "org_name": FieldSpec(
        aliases=(
            "Health Center Name",
            "Grantee Name",
            "Health Center Organization Name",
            "Organization Name",
        ),
        contains=(("healthcenter", "name"), ("grantee", "name")),
        exclude=("site",),
    ),
    "site_id": FieldSpec(
        aliases=(
            "Site Name Number",
            "Health Center Site Number",
            "Site Number",
            "Site ID",
            "BPHC Assigned Site Number",
        ),
        contains=(("site", "number"), ("site", "id")),
    ),
    "site_name": FieldSpec(
        aliases=("Site Name", "Health Center Site Name"),
        contains=(("site", "name"),),
        exclude=("number",),
    ),
    "site_street": FieldSpec(
        aliases=("Site Address", "Site Street Address", "Site Address Line 1"),
        contains=(("site", "address"),),
        exclude=("web", "email", "city", "state", "zip", "postal"),
    ),
    "site_city": FieldSpec(
        aliases=("Site City", "Site City Name"),
        contains=(("site", "city"),),
    ),
    "site_state": FieldSpec(
        aliases=("Site State Abbreviation", "Site State", "Site State Code"),
        contains=(("site", "state"),),
    ),
    "site_zip": FieldSpec(
        aliases=("Site Postal Code", "Site ZIP Code", "Site Zip"),
        contains=(("site", "postal"), ("site", "zip")),
    ),
    "site_phone": FieldSpec(
        aliases=("Site Telephone Number", "Site Phone Number"),
        contains=(("site", "telephone"), ("site", "phone")),
    ),
    "site_website": FieldSpec(
        aliases=("Site Web Address", "Health Center Web Site", "Site Website"),
        contains=(("site", "web"), ("web", "address")),
    ),
    "site_type": FieldSpec(
        aliases=(
            "Site Type Description",
            "Health Center Site Type Description",
            "Site Type",
        ),
        contains=(("site", "type"),),
    ),
    "site_status": FieldSpec(
        aliases=(
            "Site Status Description",
            "Health Center Site Status Description",
            "Site Operational Status",
        ),
        contains=(("site", "status"),),
    ),
    "health_center_type": FieldSpec(
        aliases=(
            "Health Center Type Description",
            "Health Center Type",
            "Grantee Type",
            "Health Center Program Type",
        ),
        contains=(("healthcenter", "type"), ("lookalike",)),
    ),
    # Organization-level address columns, present in some releases.
    "org_street": FieldSpec(
        aliases=("Health Center Address", "Grantee Address", "Organization Address"),
        contains=(("healthcenter", "address"), ("grantee", "address")),
        exclude=("site", "web", "email"),
    ),
    "org_city": FieldSpec(
        aliases=("Health Center City", "Grantee City"),
        contains=(("healthcenter", "city"), ("grantee", "city")),
        exclude=("site",),
    ),
    "org_state": FieldSpec(
        aliases=("Health Center State", "Grantee State", "State Abbreviation"),
        contains=(("healthcenter", "state"), ("grantee", "state")),
        exclude=("site",),
    ),
    "org_zip": FieldSpec(
        aliases=("Health Center Postal Code", "Grantee Postal Code"),
        contains=(("healthcenter", "postal"), ("grantee", "postal")),
        exclude=("site",),
    ),
}

# Fields whose absence is normal and does not degrade the result: the
# organization-level address columns fall back to the site address, and the
# rest are nice-to-have detail.
OPTIONAL_SITE_FIELDS = frozenset(
    {
        "org_street",
        "org_city",
        "org_state",
        "org_zip",
        "site_phone",
        "site_website",
        "site_type",
        "grant_number",
    }
)

# Awardee file.
AWARDEE_FIELDS: dict[str, FieldSpec] = {
    "hrsa_id": FieldSpec(
        aliases=(
            "BHCMIS Organization Identification Number",
            "BHCMISID",
            "Organization ID",
        ),
        contains=(("bhcmis",), ("organization", "identification")),
    ),
    "grant_number": FieldSpec(
        aliases=("Grant Number", "Health Center Grant Number"),
        contains=(("grant", "number"),),
    ),
    "org_name": FieldSpec(
        aliases=(
            "Health Center Name",
            "Grantee Name",
            "Awardee Name",
            "Organization Name",
        ),
        contains=(("name",),),
        exclude=("site", "contact", "project", "officer"),
    ),
    "org_street": FieldSpec(
        aliases=("Health Center Address", "Grantee Address", "Street Address"),
        contains=(("address",),),
        exclude=("web", "email", "site"),
    ),
    "org_city": FieldSpec(aliases=("Health Center City", "City"), contains=(("city",),)),
    "org_state": FieldSpec(
        aliases=("Health Center State", "State", "State Abbreviation"),
        contains=(("state",),),
    ),
    "org_zip": FieldSpec(
        aliases=("Health Center Postal Code", "Postal Code", "ZIP Code"),
        contains=(("postal",), ("zip",)),
    ),
    "award_amount": FieldSpec(
        aliases=(
            "Grant Award Total",
            "Total Award Amount",
            "Award Amount",
            "Total Grant Funding",
            "Financial Assistance Amount",
        ),
        contains=(("award", "total"), ("award", "amount"), ("grant", "amount")),
    ),
    "funding_program": FieldSpec(
        aliases=(
            "Health Center Program Funding",
            "Grant Program Description",
            "Funding Type",
            "Program",
        ),
        contains=(("program", "funding"), ("grant", "program"), ("funding", "type")),
    ),
    "health_center_type": FieldSpec(
        aliases=("Health Center Type Description", "Health Center Type"),
        contains=(("healthcenter", "type"),),
    ),
}


def resolve_columns(
    headers: Sequence[str], specs: dict[str, FieldSpec]
) -> dict[str, str]:
    """Map logical field names to the actual CSV headers present.

    Missing fields are simply absent from the result; callers treat them as
    unavailable rather than failing, so a renamed optional column degrades to a
    null value instead of breaking the run.
    """
    normalized = {header: normalize_header(header) for header in headers}
    claimed: set[str] = set()
    resolved: dict[str, str] = {}

    # Pass 1: exact alias matches, which are unambiguous.
    for field_name, spec in specs.items():
        wanted = {normalize_header(alias) for alias in spec.aliases}
        for header, norm in normalized.items():
            if header not in claimed and norm in wanted:
                resolved[field_name] = header
                claimed.add(header)
                break

    # Pass 2: keyword heuristics for renamed columns.
    for field_name, spec in specs.items():
        if field_name in resolved:
            continue
        for group in spec.contains:
            match = next(
                (
                    header
                    for header, norm in normalized.items()
                    if header not in claimed
                    and all(token in norm for token in group)
                    and not any(bad in norm for bad in spec.exclude)
                ),
                None,
            )
            if match:
                resolved[field_name] = match
                claimed.add(match)
                break

    return resolved


def _value(row: dict[str, str], columns: dict[str, str], field_name: str) -> str | None:
    header = columns.get(field_name)
    return clean(row.get(header)) if header else None


# ---------------------------------------------------------------------------
# Parsed record types
# ---------------------------------------------------------------------------


@dataclass
class SiteRecord:
    org_key: str
    hrsa_id: str | None
    org_name: str
    grant_number: str | None
    health_center_type: str | None
    site_id: str | None
    site_name: str
    street: str | None
    city: str | None
    state: str | None
    zip_code: str | None
    phone: str | None
    website: str | None
    site_type: str | None
    status: str | None
    org_street: str | None = None
    org_city: str | None = None
    org_state: str | None = None
    org_zip: str | None = None


@dataclass
class AwardeeRecord:
    hrsa_id: str | None
    org_name: str
    state: str | None
    grant_number: str | None
    award_amount: float | None
    funding_program: str | None
    health_center_type: str | None
    street: str | None = None
    city: str | None = None
    zip_code: str | None = None


@dataclass
class OrganizationRecord:
    """One deduplicated FQHC organization, ready to persist."""

    dedup_key: str
    hrsa_id: str | None
    name: str
    normalized_name: str
    street: str | None
    city: str | None
    state: str | None
    zip_code: str | None
    phone: str | None
    website: str | None
    grantee_type: GranteeType
    grant_number: str | None
    federal_award_amount: float | None
    funding_program: str | None
    funding_programs: list[str] = field(default_factory=list)
    sites: list[SiteRecord] = field(default_factory=list)

    @property
    def site_count(self) -> int:
        return len(self.sites)


@dataclass
class HrsaIngestResult:
    organizations: int = 0
    sites: int = 0
    rows_read: int = 0
    rows_skipped: int = 0
    used_cache: bool = False
    cache_date: datetime | None = None
    source_reachable: bool = True
    awardees_matched: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def status(self) -> RunStatus:
        if self.organizations == 0:
            return RunStatus.FAILED
        return RunStatus.PARTIAL if not self.source_reachable else RunStatus.SUCCESS


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------


@dataclass
class SourceLoad:
    entry: CacheEntry
    fetched_live: bool
    error: str | None = None


def _extract_csv_bytes(content: bytes, filename: str) -> bytes:
    """HRSA sometimes serves the dataset as a zip; unwrap the CSV inside it."""
    if not content.startswith(b"PK\x03\x04"):
        return content
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise SourceUnavailable(f"{filename}: zip archive contained no CSV")
        # Largest member is the data file; the others are usually readme/lookups.
        best = max(names, key=lambda n: archive.getinfo(n).file_size)
        return archive.read(best)


def load_source(
    cache: FileCache,
    url: str,
    filename: str,
    *,
    force_refresh: bool = False,
    timeout: float = 180.0,
    client: httpx.Client | None = None,
) -> SourceLoad:
    """Return a usable copy of a source file, preferring a fresh cache hit.

    Order of preference: fresh cache (unless forced) -> live download -> stale
    cache with an explanatory error. Only a missing cache *and* a failed
    download raises.
    """
    if not force_refresh and cache.is_fresh(filename):
        entry = cache.get(filename)
        assert entry is not None
        return SourceLoad(entry=entry, fetched_live=False)

    try:
        owned_client = client is None
        http = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "FQHC-Prospect-Intelligence/1.0 (+allstar.partners)"},
        )
        try:
            response = http.get(url)
            response.raise_for_status()
            content = _extract_csv_bytes(response.content, filename)
        finally:
            if owned_client:
                http.close()
    except Exception as exc:  # network, HTTP status, or malformed archive
        stale = cache.get(filename)
        if stale is not None:
            return SourceLoad(
                entry=stale,
                fetched_live=False,
                error=(
                    f"{url} unreachable ({type(exc).__name__}: {exc}); using cached "
                    f"copy from {stale.fetched_at:%Y-%m-%d}"
                ),
            )
        raise SourceUnavailable(
            f"{url} unreachable and no cached copy exists in {cache.directory}: {exc}"
        ) from exc

    return SourceLoad(entry=cache.store(filename, content, source_url=url), fetched_live=True)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _classify_grantee_type(raw: str | None) -> GranteeType:
    if not raw:
        return GranteeType.UNKNOWN
    text = raw.lower()
    if "look" in text:  # "Health Center Program Look-Alike"
        return GranteeType.LOOK_ALIKE
    if "awardee" in text or "grantee" in text or "program grant" in text:
        return GranteeType.AWARDEE
    return GranteeType.UNKNOWN


def _is_active(status: str | None) -> bool:
    """Treat a site as active unless HRSA explicitly says otherwise."""
    if not status:
        return True
    return "active" in status.lower() and "inactive" not in status.lower()


def parse_sites(text: str) -> tuple[list[SiteRecord], dict[str, str], int]:
    """Parse the service delivery site file.

    Returns the records, the resolved column map (useful for diagnostics), and
    the number of rows that could not be used.
    """
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    columns = resolve_columns(headers, SITE_FIELDS)

    if "org_name" not in columns:
        raise ValueError(
            "HRSA site file has no recognizable health center name column; "
            f"headers were: {headers[:20]}"
        )

    records: list[SiteRecord] = []
    skipped = 0

    for row in reader:
        org_name = _value(row, columns, "org_name")
        if not org_name:
            skipped += 1
            continue

        site_state = normalize_state(_value(row, columns, "site_state"))
        org_state = normalize_state(_value(row, columns, "org_state")) or site_state
        hrsa_id = _value(row, columns, "hrsa_id")

        records.append(
            SiteRecord(
                # Prefer HRSA's own organization ID; fall back to name+state.
                org_key=hrsa_id or dedup_key(org_name, org_state),
                hrsa_id=hrsa_id,
                org_name=org_name,
                grant_number=_value(row, columns, "grant_number"),
                health_center_type=_value(row, columns, "health_center_type"),
                site_id=_value(row, columns, "site_id"),
                site_name=_value(row, columns, "site_name") or org_name,
                street=_value(row, columns, "site_street"),
                city=_value(row, columns, "site_city"),
                state=site_state,
                zip_code=normalize_zip(_value(row, columns, "site_zip")),
                phone=_value(row, columns, "site_phone"),
                website=_value(row, columns, "site_website"),
                site_type=_value(row, columns, "site_type"),
                status=_value(row, columns, "site_status"),
                org_street=_value(row, columns, "org_street"),
                org_city=_value(row, columns, "org_city"),
                org_state=org_state,
                org_zip=normalize_zip(_value(row, columns, "org_zip")),
            )
        )

    return records, columns, skipped


def parse_awardees(text: str) -> tuple[list[AwardeeRecord], dict[str, str]]:
    """Parse the Health Center Program awardee file."""
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    columns = resolve_columns(headers, AWARDEE_FIELDS)

    records: list[AwardeeRecord] = []
    for row in reader:
        org_name = _value(row, columns, "org_name")
        if not org_name:
            continue
        records.append(
            AwardeeRecord(
                hrsa_id=_value(row, columns, "hrsa_id"),
                org_name=org_name,
                state=normalize_state(_value(row, columns, "org_state")),
                grant_number=_value(row, columns, "grant_number"),
                award_amount=parse_money(_value(row, columns, "award_amount")),
                funding_program=_value(row, columns, "funding_program"),
                health_center_type=_value(row, columns, "health_center_type"),
                street=_value(row, columns, "org_street"),
                city=_value(row, columns, "org_city"),
                zip_code=normalize_zip(_value(row, columns, "org_zip")),
            )
        )
    return records, columns


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _pick_org_address(sites: list[SiteRecord]) -> SiteRecord:
    """Choose the row that best represents the organization's own address.

    Preference: an administrative site, then the most frequently occurring
    address among the sites, then the first site.
    """
    administrative = [s for s in sites if s.site_type and "administrat" in s.site_type.lower()]
    if administrative:
        return administrative[0]

    counts = Counter(
        (s.street, s.city, s.state, s.zip_code) for s in sites if s.street and s.city
    )
    if counts:
        modal = counts.most_common(1)[0][0]
        for site in sites:
            if (site.street, site.city, site.state, site.zip_code) == modal:
                return site
    return sites[0]


def _majority_grantee_type(sites: Iterable[SiteRecord]) -> GranteeType:
    types = [_classify_grantee_type(s.health_center_type) for s in sites]
    known = [t for t in types if t is not GranteeType.UNKNOWN]
    if not known:
        return GranteeType.UNKNOWN
    # Awardee status wins ties: a 330 grant is the stronger, checkable fact.
    counts = Counter(known)
    top = counts.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return GranteeType.AWARDEE if GranteeType.AWARDEE in counts else top[0][0]
    return top[0][0]


def deduplicate(
    sites: list[SiteRecord], *, active_only: bool = True
) -> list[OrganizationRecord]:
    """Collapse delivery-site rows into one record per grantee organization."""
    grouped: dict[str, list[SiteRecord]] = {}
    for site in sites:
        if active_only and not _is_active(site.status):
            continue
        grouped.setdefault(site.org_key, []).append(site)

    organizations: list[OrganizationRecord] = []
    for key, group in grouped.items():
        # Collapse repeated rows for the same physical site (HRSA lists a site
        # once per program it participates in).
        unique: dict[tuple, SiteRecord] = {}
        for site in group:
            identity = (
                (site.site_id,)
                if site.site_id
                else (normalize_name(site.site_name), site.street, site.city)
            )
            unique.setdefault(identity, site)
        deduped_sites = list(unique.values())

        # The organization's name is the most common spelling in its rows.
        name = Counter(s.org_name for s in deduped_sites).most_common(1)[0][0]
        address_source = _pick_org_address(deduped_sites)
        hrsa_id = next((s.hrsa_id for s in deduped_sites if s.hrsa_id), None)
        state = (
            address_source.org_state
            or address_source.state
            or next((s.state for s in deduped_sites if s.state), None)
        )

        organizations.append(
            OrganizationRecord(
                dedup_key=key if hrsa_id else dedup_key(name, state),
                hrsa_id=hrsa_id,
                name=name,
                normalized_name=normalize_name(name),
                street=address_source.org_street or address_source.street,
                city=address_source.org_city or address_source.city,
                state=state,
                zip_code=address_source.org_zip or address_source.zip_code,
                phone=next((s.phone for s in deduped_sites if s.phone), None),
                website=next((s.website for s in deduped_sites if s.website), None),
                grantee_type=_majority_grantee_type(deduped_sites),
                grant_number=next(
                    (s.grant_number for s in deduped_sites if s.grant_number), None
                ),
                federal_award_amount=None,  # filled in from the awardee file
                funding_program=None,
                sites=sorted(deduped_sites, key=lambda s: (s.city or "", s.site_name)),
            )
        )

    return sorted(organizations, key=lambda o: o.name.lower())


def merge_awardees(
    organizations: list[OrganizationRecord], awardees: list[AwardeeRecord]
) -> int:
    """Attach Section 330 award amounts to organizations. Returns match count.

    Awardee rows are matched by HRSA ID, then grant number, then normalized
    name + state. Multiple awards for one organization are summed, since HRSA
    publishes one row per funding stream.
    """
    by_hrsa_id: dict[str, list[AwardeeRecord]] = {}
    by_grant: dict[str, list[AwardeeRecord]] = {}
    by_name: dict[str, list[AwardeeRecord]] = {}

    for record in awardees:
        if record.hrsa_id:
            by_hrsa_id.setdefault(record.hrsa_id, []).append(record)
        if record.grant_number:
            by_grant.setdefault(record.grant_number, []).append(record)
        by_name.setdefault(dedup_key(record.org_name, record.state), []).append(record)

    matched = 0
    for org in organizations:
        candidates: list[AwardeeRecord] = []
        if org.hrsa_id and org.hrsa_id in by_hrsa_id:
            candidates = by_hrsa_id[org.hrsa_id]
        elif org.grant_number and org.grant_number in by_grant:
            candidates = by_grant[org.grant_number]
        else:
            candidates = by_name.get(dedup_key(org.name, org.state), [])

        if not candidates:
            continue

        matched += 1
        amounts = [c.award_amount for c in candidates if c.award_amount is not None]
        # None (unknown) stays None -- an absent award column must not read as $0.
        org.federal_award_amount = sum(amounts) if amounts else None

        # A health center commonly holds several awards at once -- Community
        # Health Center, Health Care for the Homeless, Migrant Health -- and
        # each names a distinct programme area, so keep all of them.
        programmes: list[str] = []
        for candidate in candidates:
            if candidate.funding_program and candidate.funding_program not in programmes:
                programmes.append(candidate.funding_program)
        org.funding_programs = programmes
        org.funding_program = programmes[0] if programmes else None
        if not org.grant_number:
            org.grant_number = next(
                (c.grant_number for c in candidates if c.grant_number), None
            )
        if org.grantee_type is GranteeType.UNKNOWN:
            org.grantee_type = _majority_type_from_awardees(candidates)

    return matched


def _majority_type_from_awardees(records: list[AwardeeRecord]) -> GranteeType:
    types = [_classify_grantee_type(r.health_center_type) for r in records]
    known = [t for t in types if t is not GranteeType.UNKNOWN]
    if known:
        return Counter(known).most_common(1)[0][0]
    # Presence in the awardee file is itself evidence of a Section 330 award.
    return GranteeType.AWARDEE


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist(session: Session, organizations: list[OrganizationRecord]) -> tuple[int, int]:
    """Upsert organizations and their sites. Returns (orgs, sites) written.

    Existing rows are updated in place so that human decisions stored against
    an organization -- notably accepted or rejected EIN matches -- survive a
    re-run of the pipeline.
    """
    existing = {
        org.dedup_key: org for org in session.scalars(select(Organization)).all()
    }
    org_count = 0
    site_count = 0

    for record in organizations:
        org = existing.get(record.dedup_key)
        if org is None:
            org = Organization(dedup_key=record.dedup_key)
            session.add(org)

        org.hrsa_id = record.hrsa_id
        org.name = record.name
        org.normalized_name = record.normalized_name
        org.street = record.street
        org.city = record.city
        org.state = record.state
        org.zip_code = record.zip_code
        org.phone = record.phone
        org.website = record.website
        org.site_count = record.site_count
        org.grantee_type = record.grantee_type
        org.grant_number = record.grant_number
        org.federal_award_amount = record.federal_award_amount
        org.funding_program = record.funding_program
        org.funding_programs = record.funding_programs or None
        org.last_seen_at = utcnow()
        session.flush()  # assign org.id for new rows

        # Replace the site list wholesale: HRSA is authoritative for sites and
        # nothing user-generated hangs off them.
        for site in list(org.sites):
            session.delete(site)
        session.flush()

        for site_record in record.sites:
            session.add(
                Site(
                    organization_id=org.id,
                    site_id=site_record.site_id,
                    name=site_record.site_name,
                    street=site_record.street,
                    city=site_record.city,
                    state=site_record.state,
                    zip_code=site_record.zip_code,
                    site_type=site_record.site_type,
                    status=site_record.status,
                )
            )
            site_count += 1
        org_count += 1

    session.commit()
    return org_count, site_count


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------


def ingest(
    session: Session,
    config: Config,
    *,
    force_refresh: bool = False,
    on_progress: ProgressFn | None = None,
    client: httpx.Client | None = None,
) -> HrsaIngestResult:
    """Run the HRSA stage end to end and record it in ``ingest_runs``."""
    report = on_progress or (lambda _message: None)
    result = HrsaIngestResult()
    run = IngestRun(stage="hrsa", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    try:
        cache = FileCache(config.cache_directory, config.cache.max_age_days)

        report("Fetching HRSA service delivery sites")
        sites_load = load_source(
            cache,
            config.hrsa.sites_url,
            config.hrsa.sites_filename,
            force_refresh=force_refresh,
            timeout=config.hrsa.timeout_seconds,
            client=client,
        )
        if sites_load.error:
            result.source_reachable = False
            result.messages.append(sites_load.error)
        if not sites_load.fetched_live:
            result.used_cache = True
            result.cache_date = sites_load.entry.fetched_at

        site_records, columns, skipped = parse_sites(sites_load.entry.read_text())
        result.rows_read = len(site_records) + skipped
        result.rows_skipped = skipped
        report(f"Parsed {len(site_records):,} delivery site rows")

        # Only complain about fields that actually change the output; the
        # organization-level address columns are absent from most releases by
        # design and are covered by the site-level fallback.
        missing = sorted(set(SITE_FIELDS) - set(columns) - OPTIONAL_SITE_FIELDS)
        if missing:
            result.messages.append(
                "HRSA site file did not expose these fields: " + ", ".join(missing)
            )

        organizations = deduplicate(
            site_records, active_only=config.hrsa.active_sites_only
        )
        report(
            f"Deduplicated to {len(organizations):,} organizations "
            f"from {len(site_records):,} sites"
        )

        # The awardee file is enrichment, not a hard dependency: without it the
        # grant-dependence factor is simply unavailable.
        try:
            report("Fetching HRSA Health Center Program awardees")
            awardee_load = load_source(
                cache,
                config.hrsa.awardees_url,
                config.hrsa.awardees_filename,
                force_refresh=force_refresh,
                timeout=config.hrsa.timeout_seconds,
                client=client,
            )
            if awardee_load.error:
                result.source_reachable = False
                result.messages.append(awardee_load.error)
            if not awardee_load.fetched_live:
                result.used_cache = True
                result.cache_date = result.cache_date or awardee_load.entry.fetched_at

            awardee_records, _ = parse_awardees(awardee_load.entry.read_text())
            result.awardees_matched = merge_awardees(organizations, awardee_records)
            report(
                f"Attached award data to {result.awardees_matched:,} organizations"
            )
        except (SourceUnavailable, ValueError) as exc:
            result.source_reachable = False
            result.messages.append(
                f"Awardee file unavailable ({exc}); grant-dependence scoring will be "
                "unavailable for all organizations"
            )

        org_count, site_count = persist(session, organizations)
        result.organizations = org_count
        result.sites = site_count
        report(f"Stored {org_count:,} organizations and {site_count:,} sites")

    except Exception as exc:
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.message = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.rows_read
    run.records_written = result.organizations
    run.used_cache = result.used_cache
    run.cache_date = result.cache_date
    run.source_reachable = result.source_reachable
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result
