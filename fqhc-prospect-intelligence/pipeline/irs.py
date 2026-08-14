"""IRS Form 990 e-file XML: officers, directors and paid contractors.

ProPublica's API exposes financial totals but not the people, so Part VII comes
from the IRS's own e-file XML. Two sections matter:

* **Part VII Section A** -- officers, directors, trustees, key employees and the
  highest compensated employees, with titles, hours and reportable
  compensation. This is where board members come from.
* **Part VII Section B** -- independent contractors paid more than $100,000,
  with a description of the service. For an MSP this is the interesting one:
  it is where an incumbent IT, EHR or billing vendor shows up by name.

What is *not* here: personal contact details. A 990 lists officers care of the
organization's own address; no free authoritative source publishes their direct
emails or phone numbers, and this module will not invent them.

Two practical problems, both handled the same way as the HRSA ingest:

* **The schema has generations.** Returns filed in 2010 and 2023 use different
  element names for the same fact, and every element is namespaced. Elements are
  matched by local name against alias lists rather than by exact path.
* **The files move.** The IRS has published this data through an S3 bucket, then
  bulk ZIPs, with the URLs changing. So the fetcher is configurable and there is
  always a local-directory fallback: drop XML files in the configured folder and
  the parser uses them, no network involved.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ElementTree
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ProgressFn = Callable[[str], None]


# ---------------------------------------------------------------------------
# Namespace-agnostic element access
# ---------------------------------------------------------------------------


def normalize_ein(value: str | None) -> str | None:
    """Nine digits, or None.

    Index files sometimes over-pad an EIN with leading zeros, so anything
    longer than nine digits is trimmed from the left -- an EIN is exactly nine
    digits and the extra zeros are padding, never data.
    """
    digits = re.sub(r"[^0-9]", "", value or "")
    if not digits:
        return None
    return digits[-9:] if len(digits) > 9 else digits.zfill(9)


def local_name(tag: str) -> str:
    """Element name without its XML namespace.

    IRS returns are namespaced (``{http://www.irs.gov/efile}TaxYr``), and the
    namespace URI has changed across schema versions, so every lookup here
    works on the local name alone.
    """
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def iter_named(element: ElementTree.Element, *names: str) -> Iterator[ElementTree.Element]:
    """Every descendant whose local name is one of ``names``."""
    wanted = set(names)
    for child in element.iter():
        if local_name(child.tag) in wanted:
            yield child


def first_text(element: ElementTree.Element, *names: str) -> str | None:
    """Text of the first descendant matching any of ``names``."""
    for match in iter_named(element, *names):
        text = (match.text or "").strip()
        if text:
            return text
    return None


def first_number(element: ElementTree.Element, *names: str) -> float | None:
    """Numeric value of the first matching descendant, or None.

    Never returns 0.0 for an absent or unparseable element -- an unpaid board
    member reporting $0 and a missing figure are different facts.
    """
    text = first_text(element, *names)
    if text is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def flag_is_set(element: ElementTree.Element, *names: str) -> bool:
    """Whether a boolean checkbox element is present and true."""
    text = first_text(element, *names)
    return text is not None and text.strip().lower() in {"1", "true", "x", "yes"}


# ---------------------------------------------------------------------------
# Field aliases across schema generations
# ---------------------------------------------------------------------------

PERSON_GROUPS = ("Form990PartVIISectionAGrp", "Form990PartVIISectionA")
CONTRACTOR_GROUPS = ("ContractorCompensationGrp", "ContractorCompensation")

PERSON_NAME = ("PersonNm", "NamePerson", "NameOfPerson")
BUSINESS_NAME = ("BusinessNameLine1Txt", "BusinessNameLine1", "BusinessNameLine1Text")
TITLE = ("TitleTxt", "Title", "PersonTitle")
HOURS = ("AverageHoursPerWeekRt", "AverageHoursPerWeek", "AvrgHoursPerWeekRt")
COMPENSATION = (
    "ReportableCompFromOrgAmt",
    "ReportableCompFromOrganization",
    "CompensationAmt",
    "Compensation",
)
RELATED_COMPENSATION = (
    "ReportableCompFromRltdOrgAmt",
    "ReportableCompFromRelatedOrgs",
)
OTHER_COMPENSATION = ("OtherCompensationAmt", "OtherCompensation")

SERVICES = ("ServicesDesc", "ServicesDescription", "TypeOfService", "ServicesProvided")

# Part VII role checkboxes, newest element name first. The label is what the
# form itself calls the role.
ROLE_FLAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Board member", ("IndividualTrusteeOrDirectorInd", "IndividualTrusteeOrDirector")),
    ("Institutional trustee", ("InstitutionalTrusteeInd", "InstitutionalTrustee")),
    ("Officer", ("OfficerInd", "Officer")),
    ("Key employee", ("KeyEmployeeInd", "KeyEmployee")),
    (
        "Highest compensated employee",
        ("HighestCompensatedEmployeeInd", "HighestCompensatedEmployee"),
    ),
    (
        "Former",
        ("FormerOfcrDirectorTrusteeInd", "FormerOfficerDirectorTrustee", "Former"),
    ),
)

TAX_YEAR = ("TaxYr", "TaxYear")
EIN_FIELDS = ("EIN",)


# ---------------------------------------------------------------------------
# Parsed records
# ---------------------------------------------------------------------------


@dataclass
class PersonRecord:
    """One row of Form 990 Part VII Section A."""

    name: str
    title: str | None = None
    roles: list[str] = field(default_factory=list)
    average_hours: float | None = None
    compensation: float | None = None
    related_compensation: float | None = None
    other_compensation: float | None = None

    @property
    def is_board_member(self) -> bool:
        return any(
            role in ("Board member", "Institutional trustee") for role in self.roles
        )

    @property
    def total_compensation(self) -> float | None:
        """Sum of the reported components; None when none were reported."""
        parts = [
            value
            for value in (
                self.compensation,
                self.related_compensation,
                self.other_compensation,
            )
            if value is not None
        ]
        return sum(parts) if parts else None


@dataclass
class ContractorRecord:
    """One row of Form 990 Part VII Section B: a contractor paid over $100k."""

    name: str
    services: str | None = None
    compensation: float | None = None


@dataclass
class Form990Return:
    ein: str | None = None
    tax_year: int | None = None
    people: list[PersonRecord] = field(default_factory=list)
    contractors: list[ContractorRecord] = field(default_factory=list)

    @property
    def board_members(self) -> list[PersonRecord]:
        return [person for person in self.people if person.is_board_member]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _entity_name(group: ElementTree.Element) -> str | None:
    """A person's name, or the business name when the filer listed an entity."""
    return first_text(group, *PERSON_NAME) or first_text(group, *BUSINESS_NAME)


def _roles(group: ElementTree.Element) -> list[str]:
    return [label for label, names in ROLE_FLAGS if flag_is_set(group, *names)]


def parse_return(content: bytes | str) -> Form990Return:
    """Parse one Form 990 e-file XML document.

    Raises ``ValueError`` on XML that cannot be parsed at all. A well-formed
    return with no Part VII data yields an empty result rather than an error --
    small filers using Form 990-EZ legitimately have neither section.
    """
    try:
        root = ElementTree.fromstring(
            content if isinstance(content, bytes) else content.encode("utf-8")
        )
    except ElementTree.ParseError as exc:
        raise ValueError(f"Not valid XML: {exc}") from exc

    result = Form990Return()

    year_text = first_text(root, *TAX_YEAR)
    if year_text and year_text[:4].isdigit():
        result.tax_year = int(year_text[:4])

    result.ein = normalize_ein(first_text(root, *EIN_FIELDS))

    for group in iter_named(root, *PERSON_GROUPS):
        name = _entity_name(group)
        if not name:
            continue
        result.people.append(
            PersonRecord(
                name=name,
                title=first_text(group, *TITLE),
                roles=_roles(group),
                average_hours=first_number(group, *HOURS),
                compensation=first_number(group, *COMPENSATION),
                related_compensation=first_number(group, *RELATED_COMPENSATION),
                other_compensation=first_number(group, *OTHER_COMPENSATION),
            )
        )

    for group in iter_named(root, *CONTRACTOR_GROUPS):
        name = _entity_name(group)
        if not name:
            continue
        result.contractors.append(
            ContractorRecord(
                name=name,
                services=first_text(group, *SERVICES),
                compensation=first_number(group, *COMPENSATION),
            )
        )

    return result


# ---------------------------------------------------------------------------
# Locating XML documents
# ---------------------------------------------------------------------------

INDEX_EIN_COLUMNS = ("ein", "einnumber", "filerein")
INDEX_OBJECT_COLUMNS = ("objectid", "objid", "object_id")
INDEX_YEAR_COLUMNS = ("taxperiod", "taxyear", "taxperiodenddate")


def parse_index(text: str) -> dict[str, list[str]]:
    """Map EIN to object IDs from an IRS index file.

    Column names differ between the yearly index files, so they are resolved by
    normalized name rather than position.
    """
    reader = csv.DictReader(io.StringIO(text))
    headers = {re.sub(r"[^a-z0-9]", "", (h or "").lower()): h for h in reader.fieldnames or []}

    ein_column = next((headers[k] for k in INDEX_EIN_COLUMNS if k in headers), None)
    object_column = next(
        (headers[k] for k in INDEX_OBJECT_COLUMNS if k in headers), None
    )
    if not ein_column or not object_column:
        raise ValueError(
            "IRS index file has no recognizable EIN and object id columns; "
            f"headers were {list(headers.values())[:12]}"
        )

    index: dict[str, list[str]] = {}
    for row in reader:
        ein = normalize_ein(row.get(ein_column))
        object_id = (row.get(object_column) or "").strip()
        if not ein or not object_id:
            continue
        index.setdefault(ein, []).append(object_id)
    return index


def local_xml_for_ein(directory: Path, ein: str) -> list[Path]:
    """XML files in a local directory belonging to one EIN.

    Matches any filename containing the EIN, which covers both files named for
    the EIN and IRS object-id filenames the user has renamed. Files are returned
    newest first by name, which for object ids sorts by filing date.
    """
    if not directory.exists():
        return []
    digits = normalize_ein(ein) or ""
    matches = [
        path
        for path in directory.glob("*.xml")
        if digits in re.sub(r"[^0-9]", "", path.name)
    ]
    return sorted(matches, reverse=True)


# Reading the EIN and year straight out of the bytes is far cheaper than
# parsing every document in a bulk download just to find out whose it is.
_EIN_PATTERN = re.compile(rb"<(?:[A-Za-z0-9]+:)?EIN>\s*([0-9\- ]{9,14})\s*<")
# Alternation, not an optional suffix: the older element is TaxYear, and
# "TaxYr" plus an optional "ear" spells TaxYrear.
_YEAR_PATTERN = re.compile(rb"<(?:[A-Za-z0-9]+:)?(?:TaxYr|TaxYear)>\s*(\d{4})")


@dataclass(frozen=True)
class DocumentRef:
    """Where one Form 990 document lives, and whose it is."""

    path: Path
    member: str | None = None   # set when the document is inside a zip archive
    ein: str | None = None
    tax_year: int | None = None

    def read_bytes(self) -> bytes:
        if self.member is None:
            return self.path.read_bytes()
        with zipfile.ZipFile(self.path) as archive:
            return archive.read(self.member)

    @property
    def label(self) -> str:
        return f"{self.path.name}:{self.member}" if self.member else self.path.name


def peek(data: bytes) -> tuple[str | None, int | None]:
    """Filer EIN and tax year, read from raw bytes without parsing the XML."""
    ein_match = _EIN_PATTERN.search(data)
    year_match = _YEAR_PATTERN.search(data)
    return (
        normalize_ein(ein_match.group(1).decode("ascii", "ignore")) if ein_match else None,
        int(year_match.group(1)) if year_match else None,
    )


def _directory_signature(directory: Path) -> tuple:
    """Cheap fingerprint of a directory, so the index is rebuilt when it changes."""
    entries = sorted(
        (path.name, path.stat().st_mtime_ns, path.stat().st_size)
        for path in directory.iterdir()
        if path.is_file()
    )
    return tuple(entries)


_DOCUMENT_INDEX: dict[tuple, dict[str, list[DocumentRef]]] = {}


def document_index(directory: Path) -> dict[str, list[DocumentRef]]:
    """Map EIN to available documents, newest tax year first.

    Indexes on the EIN *inside* each document rather than on its filename. The
    IRS bulk downloads name files by object id, which contains no EIN at all, so
    filename matching would find nothing in a real download. Zip archives are
    read in place -- there is no need to unpack a multi-gigabyte download.
    """
    if not directory.exists():
        return {}

    signature = (str(directory), _directory_signature(directory))
    cached = _DOCUMENT_INDEX.get(signature)
    if cached is not None:
        return cached

    index: dict[str, list[DocumentRef]] = {}

    def record(path: Path, member: str | None, data: bytes) -> None:
        ein, year = peek(data)
        if not ein:
            return
        index.setdefault(ein, []).append(
            DocumentRef(path=path, member=member, ein=ein, tax_year=year)
        )

    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".xml":
                record(path, None, path.read_bytes())
            elif suffix == ".zip":
                with zipfile.ZipFile(path) as archive:
                    for member in archive.namelist():
                        if member.lower().endswith(".xml"):
                            record(path, member, archive.read(member))
        except (OSError, zipfile.BadZipFile):
            # A corrupt archive should cost its own contents, not the whole run.
            continue

    for refs in index.values():
        refs.sort(key=lambda ref: (ref.tax_year or 0), reverse=True)

    _DOCUMENT_INDEX[signature] = index
    return index


def reset_document_index() -> None:
    """Forget any cached document index. Used by tests."""
    _DOCUMENT_INDEX.clear()


def extract_zip_members(content: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield (name, bytes) for every XML file in a ZIP archive."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.lower().endswith(".xml"):
                yield name, archive.read(name)


def best_return(returns: list[Form990Return]) -> Form990Return | None:
    """The most recent parsed return that actually carries Part VII data."""
    usable = [r for r in returns if r.people or r.contractors]
    if not usable:
        return None
    return max(usable, key=lambda r: (r.tax_year or 0))


def summarize(result: Form990Return) -> dict[str, Any]:
    """Counts used for progress reporting."""
    return {
        "tax_year": result.tax_year,
        "people": len(result.people),
        "board_members": len(result.board_members),
        "contractors": len(result.contractors),
    }


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


@dataclass
class PeopleResult:
    eligible: int = 0
    resolved: int = 0
    people_written: int = 0
    contractors_written: int = 0
    without_documents: int = 0
    failed: int = 0
    source_reachable: bool = True
    messages: list[str] = field(default_factory=list)

    @property
    def status(self):
        from app.models import RunStatus

        if self.eligible and self.resolved == 0 and self.failed:
            return RunStatus.FAILED
        return RunStatus.SUCCESS if self.source_reachable else RunStatus.PARTIAL


def _documents_for_ein(config, ein: str, client=None) -> list[bytes]:
    """Every 990 XML document available for one EIN, newest first.

    Local files are preferred over the network: they cost nothing, and a user
    who has downloaded the IRS bulk data has the authoritative copy already.
    """
    documents: list[bytes] = []

    directory = config.resolve(config.irs.local_directory)
    refs = document_index(directory).get(normalize_ein(ein) or "", [])
    for ref in refs[: config.irs.documents_per_org]:
        try:
            documents.append(ref.read_bytes())
        except (OSError, zipfile.BadZipFile):
            continue

    if documents or not config.irs.fetch_remote or not config.irs.xml_url_template:
        return documents

    # Remote fetch needs an object id, which only the IRS index provides.
    object_ids = _index(config, client).get(normalize_ein(ein) or "", [])
    if not object_ids or client is None:
        return documents

    for object_id in sorted(object_ids, reverse=True)[: config.irs.documents_per_org]:
        url = config.irs.xml_url_template.format(object_id=object_id)
        try:
            response = client.get(url, timeout=config.irs.timeout_seconds)
            response.raise_for_status()
        except Exception:
            continue
        documents.append(response.content)

    return documents


_INDEX_CACHE: dict[int, dict[str, list[str]]] = {}


def _index(config, client) -> dict[str, list[str]]:
    """Load and cache the EIN to object-id index, if one is configured."""
    key = id(config)
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]

    index: dict[str, list[str]] = {}
    directory = config.resolve(config.irs.local_directory)

    for path in sorted(directory.glob("index*.csv")) if directory.exists() else []:
        try:
            index.update(parse_index(path.read_text(encoding="utf-8-sig")))
        except (ValueError, OSError):
            continue

    if not index and client is not None:
        for url in config.irs.index_urls:
            try:
                response = client.get(url, timeout=config.irs.timeout_seconds)
                response.raise_for_status()
                index.update(parse_index(response.text))
            except Exception:
                continue

    _INDEX_CACHE[key] = index
    return index


def reset_index_cache() -> None:
    """Forget any cached IRS index. Used by tests."""
    _INDEX_CACHE.clear()


def enrich_people(
    session,
    config,
    *,
    client=None,
    limit: int | None = None,
    on_progress: ProgressFn | None = None,
) -> PeopleResult:
    """Populate people and contractors from Form 990 Part VII."""
    from sqlalchemy import select

    from app.models import (
        Contractor,
        EinMatch,
        IngestRun,
        MatchStatus,
        Organization,
        Person,
        RunStatus,
        utcnow,
    )

    report = on_progress or (lambda _message: None)
    result = PeopleResult()

    run = IngestRun(stage="people", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    try:
        statement = (
            select(Organization)
            .join(EinMatch, EinMatch.organization_id == Organization.id)
            .where(
                EinMatch.ein.is_not(None),
                EinMatch.status.in_(
                    [MatchStatus.AUTO.value, MatchStatus.ACCEPTED.value]
                ),
            )
            .order_by(Organization.name)
        )
        footprint = config.api_states
        if footprint:
            statement = statement.where(Organization.state.in_(footprint))

        organizations = session.scalars(statement).all()
        result.eligible = len(organizations)
        report(f"{result.eligible:,} organizations have a confirmed EIN")

        for organization in organizations:
            if limit is not None and result.resolved >= limit:
                break

            ein = organization.ein
            if not ein:
                continue

            documents = _documents_for_ein(config, ein, client)
            if not documents:
                result.without_documents += 1
                continue

            parsed: list[Form990Return] = []
            for document in documents:
                try:
                    parsed.append(parse_return(document))
                except ValueError:
                    result.failed += 1

            chosen = best_return(parsed)
            if chosen is None:
                result.without_documents += 1
                continue

            year = chosen.tax_year or 0
            result.resolved += 1

            # Replace this EIN and year wholesale: the filing is the unit of
            # truth, and a re-parse should not leave half of a previous one.
            for row in session.scalars(
                select(Person).where(Person.ein == ein, Person.tax_year == year)
            ).all():
                session.delete(row)
            for row in session.scalars(
                select(Contractor).where(
                    Contractor.ein == ein, Contractor.tax_year == year
                )
            ).all():
                session.delete(row)
            session.flush()

            for person in chosen.people:
                session.add(
                    Person(
                        ein=ein,
                        tax_year=year,
                        name=person.name,
                        title=person.title,
                        roles=person.roles or None,
                        average_hours=person.average_hours,
                        compensation=person.compensation,
                        related_compensation=person.related_compensation,
                        other_compensation=person.other_compensation,
                    )
                )
                result.people_written += 1

            for contractor in chosen.contractors:
                session.add(
                    Contractor(
                        ein=ein,
                        tax_year=year,
                        name=contractor.name,
                        services=contractor.services,
                        compensation=contractor.compensation,
                    )
                )
                result.contractors_written += 1

        session.commit()
    except Exception as exc:
        session.rollback()
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.message = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise

    if result.without_documents:
        result.messages.append(
            f"No Form 990 XML found for {result.without_documents:,} of "
            f"{result.eligible:,} organizations (see irs.local_directory in "
            "config.yaml)"
        )

    report(
        f"Stored {result.people_written:,} people and "
        f"{result.contractors_written:,} contractors for {result.resolved:,} "
        "organizations"
    )

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.eligible
    run.records_written = result.people_written + result.contractors_written
    run.source_reachable = result.source_reachable
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result
