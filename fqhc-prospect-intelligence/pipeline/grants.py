"""Grants awarded to health centers, from two free sources.

**Form 990 Schedule I.** A nonprofit reports every grant it *makes* and none of
the grants it *receives*. So there is no such thing as reading a health
center's own return to find out who funded it: the only way is to read
everybody else's Schedule I and look for its EIN. That is what
:func:`scan_schedule_i` does, over the Form 990 XML already on disk. It is
expensive -- most returns carry no Schedule I at all -- so a cheap substring
test rejects those before any XML is parsed.

**A federal award file.** USAspending publishes assistance awards, with an
award number, an awarding agency, a CFDA/Assistance Listing number and a period
of performance. Agencies publish their own extracts in much the same shape.
Neither is fetched: the download URLs move, and a file on disk works offline.
Columns are resolved by name rather than by position, so an export with
different headers still loads.

Two rules run through the whole module:

* **A grant is attributed on an exact nine-digit EIN and nothing else.** Never
  on a name, however close. Attaching somebody else's money to an organization
  is exactly the kind of error this database cannot afford.
* **Nothing is described as "active" without a reported end date.** A Schedule
  I grant has no period of performance, so it is presented as history: what
  this organization was given, by whom, in a stated year.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.hrsa import FieldSpec, resolve_columns
from pipeline.irs import (
    DocumentRef,
    document_index,
    has_schedule_i,
    normalize_ein,
    parse_schedule_i,
)
from pipeline.text import clean, parse_money

ProgressFn = Callable[[str], None]

# Federal award exports: USAspending's own column names first, then the
# variations agencies ship. Anything unresolved is simply absent -- a missing
# optional column becomes a null, not a failed run.
AWARD_FIELDS: dict[str, FieldSpec] = {
    "recipient_ein": FieldSpec(
        aliases=("recipient_ein", "ein", "recipient_tax_id", "tax_id"),
        contains=(("recipient", "ein"), ("employer", "identification")),
    ),
    "recipient_name": FieldSpec(
        aliases=("recipient_name", "awardee_name", "organization_name", "recipient"),
        contains=(("recipient", "name"), ("awardee", "name")),
    ),
    "award_number": FieldSpec(
        aliases=(
            "award_id_fain",
            "fain",
            "federal_award_id_number",
            "award_number",
            "grant_number",
        ),
        contains=(("award", "id"), ("grant", "number")),
    ),
    "amount": FieldSpec(
        aliases=(
            "total_obligated_amount",
            "federal_action_obligation",
            "obligated_amount",
            "award_amount",
            "total_funding_amount",
            "amount",
        ),
        contains=(("obligat", "amount"), ("award", "amount"), ("total", "funding")),
        exclude=("non_federal", "nonfederal", "face_value"),
    ),
    "awarding_agency": FieldSpec(
        aliases=(
            "awarding_agency_name",
            "awarding_sub_agency_name",
            "agency_name",
            "awarding_agency",
        ),
        contains=(("awarding", "agency"), ("agency", "name")),
    ),
    "program_title": FieldSpec(
        aliases=(
            "cfda_title",
            "assistance_listing_title",
            "program_title",
            "award_description",
        ),
        contains=(("cfda", "title"), ("assistance", "title"), ("program", "title")),
    ),
    "cfda_number": FieldSpec(
        aliases=("cfda_number", "assistance_listing_number", "cfda"),
        contains=(("cfda", "number"), ("assistance", "listing", "number")),
    ),
    "start_date": FieldSpec(
        aliases=(
            "period_of_performance_start_date",
            "start_date",
            "award_start_date",
            "project_start_date",
        ),
        contains=(("performance", "start"), ("start", "date")),
    ),
    "end_date": FieldSpec(
        aliases=(
            "period_of_performance_current_end_date",
            "period_of_performance_end_date",
            "end_date",
            "award_end_date",
            "project_end_date",
        ),
        contains=(("performance", "end"), ("end", "date")),
    ),
    "purpose": FieldSpec(
        aliases=("award_description", "prime_award_base_transaction_description"),
        contains=(("award", "description"), ("project", "description")),
    ),
}

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%d %b %Y", "%Y%m%d")


def parse_date(value: str | None) -> datetime | None:
    """A date from an award file, or None. Never today's date as a fallback."""
    text = (value or "").strip()
    if not text:
        return None
    # Some exports carry a timestamp; the date is all that is meaningful here.
    text = text.split("T")[0].split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


@dataclass
class AwardRecord:
    """One row of a federal award file, normalized."""

    recipient_ein: str | None = None
    recipient_name: str | None = None
    award_number: str | None = None
    amount: float | None = None
    awarding_agency: str | None = None
    program_title: str | None = None
    cfda_number: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    purpose: str | None = None


def read_award_file(path: Path) -> tuple[list[AwardRecord], list[str]]:
    """Read one federal award CSV. Returns the rows and any warnings.

    A file whose headers carry no recipient EIN is rejected rather than
    guessed at: without an EIN there is no safe way to attach a row to an
    organization, and matching on name is precisely what this module refuses
    to do.
    """
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return [], [f"{path.name} could not be read: {exc}"]

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    if not headers:
        return [], [f"{path.name} has no header row"]

    columns = resolve_columns(headers, AWARD_FIELDS)
    if "recipient_ein" not in columns:
        return [], [
            f"{path.name} has no recipient EIN column, so its rows cannot be "
            "attached to an organization. A grant is only ever matched on an "
            "exact EIN, never on a name. Columns found: "
            + ", ".join(headers[:12])
        ]
    if "amount" not in columns:
        warnings.append(
            f"{path.name} has no award amount column; its rows will load with "
            "the amount shown as not available"
        )

    def value(row: dict[str, str], key: str) -> str | None:
        column = columns.get(key)
        return clean(row.get(column)) if column else None

    records: list[AwardRecord] = []
    for row in reader:
        ein = normalize_ein(value(row, "recipient_ein"))
        if not ein:
            continue
        records.append(
            AwardRecord(
                recipient_ein=ein,
                recipient_name=value(row, "recipient_name"),
                award_number=value(row, "award_number"),
                amount=parse_money(value(row, "amount")),
                awarding_agency=value(row, "awarding_agency"),
                program_title=value(row, "program_title"),
                cfda_number=value(row, "cfda_number"),
                start_date=parse_date(value(row, "start_date")),
                end_date=parse_date(value(row, "end_date")),
                purpose=value(row, "purpose"),
            )
        )

    return records, warnings


def award_files(directory: Path) -> list[Path]:
    """Every CSV in the grants directory, ignoring spreadsheet lock files."""
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".csv"
        and not path.name.startswith((".", "~$"))
    )


# ---------------------------------------------------------------------------
# Schedule I: reading everybody else's return
# ---------------------------------------------------------------------------


def iter_documents(directory: Path) -> Iterator[DocumentRef]:
    """Every Form 990 document on disk, once each.

    The index is keyed by EIN, so the same document can be reached more than
    once across overlapping downloads; ``label`` identifies a document
    uniquely (archive plus member, or the file path) and is used to de-duplicate.
    """
    seen: set[str] = set()
    for refs in document_index(directory).values():
        for ref in refs:
            if ref.label in seen:
                continue
            seen.add(ref.label)
            yield ref


def iter_document_bytes(directory: Path) -> Iterator[tuple[DocumentRef, bytes | None]]:
    """Every document's bytes, reading each archive once.

    ``DocumentRef.read_bytes`` opens and closes the containing zip for every
    member, which is fine for the three documents the people stage wants and
    ruinous across a whole download -- a large archive would be reopened tens
    of thousands of times. Documents are therefore grouped by the file they
    live in and each archive is opened once.

    Yields ``(ref, None)`` for a document that could not be read, so the caller
    can count it rather than have it disappear.
    """
    loose: list[DocumentRef] = []
    archived: dict[Path, list[DocumentRef]] = {}

    for ref in iter_documents(directory):
        if ref.member is None:
            loose.append(ref)
        else:
            archived.setdefault(ref.path, []).append(ref)

    for ref in loose:
        try:
            yield ref, ref.path.read_bytes()
        except OSError:
            yield ref, None

    for path, refs in archived.items():
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile):
            for ref in refs:
                yield ref, None
            continue
        with archive:
            for ref in refs:
                try:
                    yield ref, archive.read(ref.member)
                except (OSError, KeyError, NotImplementedError, RuntimeError):
                    # NotImplementedError is Deflate64, which Python cannot
                    # decompress; it is reported elsewhere, not fatal here.
                    yield ref, None


@dataclass
class ScheduleIResult:
    documents_read: int = 0
    documents_with_schedule_i: int = 0
    grants_found: int = 0
    failed: int = 0


def scan_schedule_i(
    directory: Path,
    wanted_eins: set[str],
    *,
    on_progress: ProgressFn | None = None,
    minimum_amount: float = 0.0,
) -> tuple[list[tuple[str, object]], ScheduleIResult]:
    """Find grants made *to* ``wanted_eins`` anywhere in the XML corpus.

    Returns ``[(recipient_ein, GrantorReturn-with-one-grant), ...]`` and a
    tally of what was read. Every document is opened, but only those whose
    bytes mention Schedule I are parsed.
    """
    report = on_progress or (lambda _message: None)
    result = ScheduleIResult()
    found: list[tuple[str, object]] = []

    if not wanted_eins:
        return found, result

    for _ref, data in iter_document_bytes(directory):
        result.documents_read += 1
        if result.documents_read % 20_000 == 0:
            report(
                f"Read {result.documents_read:,} returns, "
                f"{result.grants_found:,} grants found so far"
            )

        if data is None:
            result.failed += 1
            continue

        if not has_schedule_i(data):
            continue
        result.documents_with_schedule_i += 1

        try:
            grantor = parse_schedule_i(data)
        except ValueError:
            result.failed += 1
            continue

        for grant in grantor.grants:
            if grant.recipient_ein not in wanted_eins:
                continue
            total = grant.total
            if minimum_amount and (total is None or total < minimum_amount):
                continue
            found.append((grant.recipient_ein, (grantor, grant)))
            result.grants_found += 1

    return found, result


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


@dataclass
class GrantResult:
    schedule_i_written: int = 0
    awards_written: int = 0
    organizations_with_grants: int = 0
    files_read: int = 0
    documents_scanned: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def status(self):
        from app.models import RunStatus

        return RunStatus.SUCCESS


def ingest(
    session: Session,
    config,
    *,
    on_progress: ProgressFn | None = None,
) -> GrantResult:
    """Load grants awarded to the organizations in this database."""
    from app.models import Grant, GrantSource, IngestRun, Organization, RunStatus, utcnow

    report = on_progress or (lambda _message: None)
    result = GrantResult()

    run = IngestRun(stage="grants", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    try:
        organizations = session.scalars(select(Organization)).all()
        by_ein: dict[str, list[int]] = {}
        for organization in organizations:
            ein = organization.ein
            if ein:
                by_ein.setdefault(ein, []).append(organization.id)

        if not by_ein:
            result.messages.append(
                "No organization has a confirmed EIN yet, so no grant can be "
                "attached to one. Run `python -m pipeline.run --stage hrsa` "
                "then `--stage ein` first."
            )
            report(result.messages[-1])

        touched: set[int] = set()

        # --- Federal award files -------------------------------------------
        directory = config.resolve(config.grants.local_directory)
        files = award_files(directory)
        if not files:
            report(f"No federal award file in {directory}")
            result.messages.append(
                f"No federal award file found in {directory}. Download an "
                "assistance award export from https://www.usaspending.gov/ "
                "(filter by recipient state and Assistance Listing 93.224 for "
                "the Health Center Program) and drop the CSV there."
            )
        # An award file is a snapshot, so this source is replaced wholesale
        # rather than merged -- a re-run must not leave last month's rows
        # behind. Cleared before anything is added, because a delete issued
        # afterwards would autoflush the new rows first and then remove them.
        if files:
            session.query(Grant).filter(
                Grant.source == GrantSource.FEDERAL_AWARD
            ).delete(synchronize_session=False)
            session.flush()

        for path in files:
            records, warnings = read_award_file(path)
            result.files_read += 1
            result.messages.extend(warnings)
            for warning in warnings:
                report(warning)

            matched = 0
            for record in records:
                for organization_id in by_ein.get(record.recipient_ein or "", []):
                    session.add(
                        Grant(
                            organization_id=organization_id,
                            source=GrantSource.FEDERAL_AWARD,
                            grantor_name=record.awarding_agency,
                            amount=record.amount,
                            purpose=record.purpose,
                            award_number=record.award_number,
                            awarding_agency=record.awarding_agency,
                            program_title=record.program_title,
                            cfda_number=record.cfda_number,
                            start_date=record.start_date,
                            end_date=record.end_date,
                            source_file=path.name,
                        )
                    )
                    touched.add(organization_id)
                    matched += 1
            result.awards_written += matched
            report(
                f"{path.name}: {len(records):,} award rows, {matched:,} matched "
                "an organization in this database"
            )

        # --- Schedule I ----------------------------------------------------
        if config.grants.scan_schedule_i:
            xml_directory = config.resolve(config.irs.local_directory)
            report(
                "Scanning Form 990 returns for grants made to these "
                "organizations. Every nonprofit reports the grants it gives "
                "and none of the grants it gets, so this reads the whole "
                "download."
            )
            found, scan = scan_schedule_i(
                xml_directory,
                set(by_ein),
                on_progress=report,
                minimum_amount=config.grants.minimum_amount,
            )
            result.documents_scanned = scan.documents_read

            session.query(Grant).filter(
                Grant.source == GrantSource.SCHEDULE_I
            ).delete(synchronize_session=False)
            session.flush()

            for recipient_ein, payload in found:
                grantor, grant = payload
                for organization_id in by_ein.get(recipient_ein, []):
                    session.add(
                        Grant(
                            organization_id=organization_id,
                            source=GrantSource.SCHEDULE_I,
                            grantor_name=grantor.grantor_name,
                            grantor_ein=grantor.grantor_ein,
                            amount=grant.total,
                            cash_amount=grant.cash,
                            non_cash_amount=grant.non_cash,
                            purpose=grant.purpose,
                            tax_year=grantor.tax_year,
                        )
                    )
                    touched.add(organization_id)
                    result.schedule_i_written += 1

            report(
                f"Read {scan.documents_read:,} returns, "
                f"{scan.documents_with_schedule_i:,} with a Schedule I, and "
                f"found {result.schedule_i_written:,} grants to organizations "
                "in this database"
            )
            if scan.documents_read == 0:
                result.messages.append(
                    f"No Form 990 XML found in {xml_directory}, so no Schedule "
                    "I could be read. See irs.local_directory in config.yaml."
                )
        else:
            result.messages.append(
                "Schedule I scanning is off. Set grants.scan_schedule_i to "
                "true in config.yaml to read who has funded these "
                "organizations out of other nonprofits' Form 990 filings."
            )

        result.organizations_with_grants = len(touched)
        session.commit()

    except Exception as exc:
        session.rollback()
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.message = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.documents_scanned or result.files_read
    run.records_written = result.schedule_i_written + result.awards_written
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result
