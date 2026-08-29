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
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ElementTree
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # pragma: no cover - depends on what is installed
    # Optional. Importing it teaches the standard library's zipfile to read
    # Deflate64 members, which the largest IRS archives use and Python cannot
    # otherwise decompress. Absent, those archives are reported and skipped
    # rather than failing the run.
    import zipfile_deflate64  # noqa: F401
except ImportError:  # pragma: no cover - the common case
    pass

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


def first_int(element: ElementTree.Element, *names: str) -> int | None:
    """Whole-number value of the first matching descendant, or None."""
    value = first_number(element, *names)
    return int(value) if value is not None else None


def tri_flag(element: ElementTree.Element, *names: str) -> bool | None:
    """Three-state checkbox: True, False, or None when the box is absent.

    ``flag_is_set`` collapses "the filer answered no" into "the filer did not
    answer", which is fine for a Part VII role but wrong for a yes/no question:
    "this organization is not subject to a single audit" is a fact worth
    displaying, and "the return does not say" is not.
    """
    for match in iter_named(element, *names):
        text = (match.text or "").strip().lower()
        if text in {"1", "true", "x", "yes"}:
            return True
        if text in {"0", "false", "no"}:
            return False
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

# --- Part I / Part III: what the organization is and does -------------------
#
# These are the facts a profile page opens with -- how old the organization is,
# how many people it employs, what it says it exists to do. All of them are on
# the face of the return; none of them were being read.

MISSION = (
    "ActivityOrMissionDesc",       # Part I line 1, one line
    "MissionDesc",                 # Part III line 1, the fuller statement
    "PrimaryExemptPurposeTxt",
    "ActivityOrMissionDescription",
)
FORMATION_YEAR = ("FormationYr", "YearFormation", "FormationYear")
DOMICILE_STATE = ("LegalDomicileStateCd", "StateLegalDomicile", "LegalDomicileSt")
WEBSITE = ("WebsiteAddressTxt", "WebSite", "InternetWebSiteAddress")
EMPLOYEE_COUNT = (
    "TotalEmployeeCnt",            # Part I line 5
    "TotalNbrEmployees",
    "TotalNumberEmployees",
)
VOLUNTEER_COUNT = ("TotalVolunteersCnt", "TotalNbrVolunteers", "TotalNumberVolunteers")

# --- Balance sheet and functional expenses ---------------------------------
#
# Liabilities are the one headline figure ProPublica's summary does not carry,
# and without them a balance sheet is half a story.

TOTAL_ASSETS_EOY = ("TotalAssetsEOYAmt", "TotalAssetsEOY", "TotalAssetsEndOfYear")
TOTAL_LIABILITIES_EOY = (
    "TotalLiabilitiesEOYAmt",
    "TotalLiabilitiesEOY",
    "TotalLiabilitiesEndOfYear",
)
NET_ASSETS_EOY = (
    "NetAssetsOrFundBalancesEOYAmt",
    "NetAssetsOrFundBalancesEOY",
    "TotalNetAssetsFundBalanceEOY",
)
CY_REVENUE = ("CYTotalRevenueAmt", "TotalRevenueCurrentYear")
CY_EXPENSES = ("CYTotalExpensesAmt", "TotalExpensesCurrentYear")
CY_FUNDRAISING_EXPENSE = ("CYTotalFundraisingExpenseAmt", "TotalFundrsngExpCurrentYear")
CY_GRANTS_PAID = ("CYGrantsAndSimilarPaidAmt", "GrantsAndSimilarAmntsCY")
CY_SALARIES = ("CYSalariesCompEmpBnftPaidAmt", "SalariesEtcCurrentYear")

# Part IX totals row, split across the three functional columns.
FUNCTIONAL_TOTALS_GROUP = ("TotalFunctionalExpensesGrp", "TotalFunctionalExpenses")
PROGRAM_SERVICES_AMOUNT = ("ProgramServicesAmt", "ProgramServices")
MANAGEMENT_AMOUNT = ("ManagementAndGeneralAmt", "ManagementAndGeneral")
FUNDRAISING_AMOUNT = ("FundraisingAmt", "Fundraising")

# --- Part XII / Schedule A: how the books were checked ----------------------
#
# A health center living on federal money is subject to a Single Audit, and
# whether one was required and performed is a compliance fact -- exactly the
# ground an IT proposal about controls and evidence stands on.

FS_AUDITED = ("FSAuditedInd", "FinancialStatementsAudited", "AuditedInd")
FS_REVIEWED = ("FSReviewedInd", "FinancialStatementsCompiled")
SINGLE_AUDIT_REQUIRED = (
    "FederalGrantAuditRequiredInd",
    "FederalGrantAuditRequired",
    "AuditRequiredOrPerformed",
)
SINGLE_AUDIT_PERFORMED = ("FederalGrantAuditPerformedInd", "FederalGrantAuditPerformed")
AUDIT_COMMITTEE = ("AuditCommitteeInd", "AuditCommittee")

# --- Part III: program service accomplishments -----------------------------

PROGRAM_GROUPS = ("ProgramSrvcAccomplishmentGrp", "ProgramServiceAccomplishment")
PROGRAM_DESCRIPTION = (
    "Desc",
    "DescriptionProgramSrvcAccomTxt",
    "Description",
    "ProgramServiceAccomplishmentDesc",
)
PROGRAM_EXPENSE = ("ExpenseAmt", "Expense", "ExpenseAmount")
PROGRAM_GRANT = ("GrantAmt", "Grants", "GrantAmount")
PROGRAM_REVENUE = ("RevenueAmt", "Revenue", "RevenueAmount")
PROGRAM_ACTIVITY_CODE = ("ActivityCd", "ActivityCode")

# --- Schedule I: grants this filer made to other organizations -------------
#
# Read from the *grantor's* return, which is the only place a grant is
# reported: a nonprofit does not list the grants it receives anywhere on its
# own 990. So the way to learn what an FQHC was granted is to read everybody
# else's Schedule I and look for its EIN.

SCHEDULE_I_MARKER = "ScheduleI"
GRANT_GROUPS = (
    "RecipientTable",
    "GrantsOtherAsstToDomesticOrgGrp",
    "RecipientEinTbl",
)
GRANT_RECIPIENT_EIN = ("RecipientEIN", "EINOfRecipient", "RecipientEin")
GRANT_RECIPIENT_NAME = (
    "RecipientBusinessName",
    "RecipientNameBusiness",
    "BusinessNameLine1Txt",
    "BusinessNameLine1",
)
GRANT_CASH = ("CashGrantAmt", "AmountOfCashGrant", "CashGrantAmount")
GRANT_NON_CASH = (
    "NonCashAssistanceAmt",
    "AmountOfNonCashAssistance",
    "NonCashAssistanceAmount",
)
GRANT_PURPOSE = (
    "PurposeOfGrantTxt",
    "PurposeOfGrant",
    "PurposeOfGrantOrAssistance",
)
GRANT_SECTION = ("IRCSectionDesc", "IRCSectionTxt", "IRCSection")
GRANT_STATE = ("StateAbbreviationCd", "State")
FILER_NAME = ("BusinessNameLine1Txt", "BusinessNameLine1", "Name")


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
class ProgramRecord:
    """One program service accomplishment from Form 990 Part III.

    A health center describes each of its programs here in its own words, with
    the money spent on it and the money it brought in. It is the only free
    source that says what an organization actually *runs*, as opposed to what
    it is classified as.
    """

    description: str | None = None
    expenses: float | None = None
    grants: float | None = None
    revenue: float | None = None
    activity_code: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.description is None and self.expenses is None and (
            self.revenue is None
        )

    @property
    def headline(self) -> str | None:
        """The opening sentence, for a table cell that has one line to work with."""
        if not self.description:
            return None
        text = " ".join(self.description.split())
        cut = text.find(". ")
        if 0 < cut < 160:
            return text[: cut + 1]
        return text if len(text) <= 160 else text[:157].rsplit(" ", 1)[0] + "..."


@dataclass
class GrantRecord:
    """One Schedule I row: a grant this filer made to another organization."""

    recipient_ein: str | None = None
    recipient_name: str | None = None
    cash: float | None = None
    non_cash: float | None = None
    purpose: str | None = None
    section: str | None = None
    state: str | None = None

    @property
    def total(self) -> float | None:
        """Cash plus non-cash, counting only what was actually reported."""
        parts = [value for value in (self.cash, self.non_cash) if value is not None]
        return sum(parts) if parts else None


@dataclass
class GrantorReturn:
    """A filer's Schedule I: who it is, and everyone it granted money to."""

    grantor_ein: str | None = None
    grantor_name: str | None = None
    tax_year: int | None = None
    grants: list[GrantRecord] = field(default_factory=list)


@dataclass
class OrganizationFacts:
    """Facts about the filer itself, from the face of the return.

    Everything here is nullable and nothing is derived by arithmetic from
    something else. A return that omits a line leaves the field None, and the
    UI says "Not available" -- an organization that reports no volunteers and
    one whose return is silent about volunteers are different organizations.
    """

    mission: str | None = None
    formation_year: int | None = None
    domicile_state: str | None = None
    website: str | None = None
    employee_count: int | None = None
    volunteer_count: int | None = None

    total_revenue: float | None = None
    total_expenses: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    net_assets: float | None = None

    program_expenses: float | None = None
    management_expenses: float | None = None
    fundraising_expenses: float | None = None
    grants_paid: float | None = None
    salaries: float | None = None

    financials_audited: bool | None = None
    single_audit_required: bool | None = None
    single_audit_performed: bool | None = None
    audit_committee: bool | None = None

    @property
    def has_balance_sheet(self) -> bool:
        return any(
            value is not None
            for value in (self.total_assets, self.total_liabilities, self.net_assets)
        )

    @property
    def has_expense_split(self) -> bool:
        return any(
            value is not None
            for value in (
                self.program_expenses,
                self.management_expenses,
                self.fundraising_expenses,
            )
        )

    @property
    def has_any(self) -> bool:
        """Whether the return yielded anything at all worth storing."""
        return any(
            value is not None
            for value in (
                self.mission,
                self.formation_year,
                self.domicile_state,
                self.website,
                self.employee_count,
                self.volunteer_count,
                self.total_revenue,
                self.total_expenses,
                self.total_assets,
                self.total_liabilities,
                self.net_assets,
                self.program_expenses,
                self.management_expenses,
                self.fundraising_expenses,
                self.grants_paid,
                self.salaries,
                self.financials_audited,
                self.single_audit_required,
                self.single_audit_performed,
                self.audit_committee,
            )
        )


@dataclass
class Form990Return:
    ein: str | None = None
    tax_year: int | None = None
    people: list[PersonRecord] = field(default_factory=list)
    contractors: list[ContractorRecord] = field(default_factory=list)
    programs: list[ProgramRecord] = field(default_factory=list)
    facts: OrganizationFacts = field(default_factory=OrganizationFacts)

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


def _functional_expenses(root: ElementTree.Element) -> tuple[
    float | None, float | None, float | None
]:
    """Program / management / fundraising totals from the Part IX totals row.

    Read from inside the totals group only. The three column names repeat on
    every one of the twenty-odd expense lines, so a document-wide search would
    return the first line item -- grants paid -- and label it "program
    services".
    """
    for group in iter_named(root, *FUNCTIONAL_TOTALS_GROUP):
        return (
            first_number(group, *PROGRAM_SERVICES_AMOUNT),
            first_number(group, *MANAGEMENT_AMOUNT),
            first_number(group, *FUNDRAISING_AMOUNT),
        )
    return (None, None, None)


def _parse_facts(root: ElementTree.Element) -> OrganizationFacts:
    """Read the filer's own description and headline figures off the return."""
    program, management, fundraising = _functional_expenses(root)

    year = first_int(root, *FORMATION_YEAR)
    # A formation year outside living memory of the tax code is a parse
    # accident, not a fact about the organization.
    if year is not None and not 1600 <= year <= datetime.now().year:
        year = None

    domicile = first_text(root, *DOMICILE_STATE)

    return OrganizationFacts(
        mission=_clean_prose(first_text(root, *MISSION)),
        formation_year=year,
        domicile_state=domicile[:2].upper() if domicile and len(domicile) >= 2 else None,
        website=first_text(root, *WEBSITE),
        employee_count=first_int(root, *EMPLOYEE_COUNT),
        volunteer_count=first_int(root, *VOLUNTEER_COUNT),
        total_revenue=first_number(root, *CY_REVENUE),
        total_expenses=first_number(root, *CY_EXPENSES),
        total_assets=first_number(root, *TOTAL_ASSETS_EOY),
        total_liabilities=first_number(root, *TOTAL_LIABILITIES_EOY),
        net_assets=first_number(root, *NET_ASSETS_EOY),
        program_expenses=program,
        management_expenses=management,
        fundraising_expenses=(
            fundraising
            if fundraising is not None
            else first_number(root, *CY_FUNDRAISING_EXPENSE)
        ),
        grants_paid=first_number(root, *CY_GRANTS_PAID),
        salaries=first_number(root, *CY_SALARIES),
        financials_audited=tri_flag(root, *FS_AUDITED),
        single_audit_required=tri_flag(root, *SINGLE_AUDIT_REQUIRED),
        single_audit_performed=tri_flag(root, *SINGLE_AUDIT_PERFORMED),
        audit_committee=tri_flag(root, *AUDIT_COMMITTEE),
    )


def _clean_prose(text: str | None) -> str | None:
    """Collapse the whitespace a filer's word processor left in a narrative."""
    if not text:
        return None
    collapsed = " ".join(text.split())
    return collapsed or None


def has_schedule_i(data: bytes) -> bool:
    """Cheap pre-check: does this document contain a Schedule I at all?

    Reading grants means looking at every return in the download, not just the
    ones belonging to health centers, because any nonprofit might be the
    grantor. Most returns carry no Schedule I, and a substring test on the raw
    bytes rejects them for a fraction of the cost of building an element tree.
    A false positive here is harmless -- the parse simply finds no grant rows.
    """
    return SCHEDULE_I_MARKER.encode("ascii") in data


def parse_schedule_i(content: bytes | str) -> GrantorReturn:
    """Parse the grants one filer reported making, from its Schedule I.

    Rows without a recipient EIN are skipped: Schedule I also covers grants to
    individuals and to organizations the filer did not identify, and neither
    can be attached to a health center with any confidence. A grant is only
    ever attributed on an exact nine-digit EIN match -- never on a name.
    """
    try:
        root = ElementTree.fromstring(
            content if isinstance(content, bytes) else content.encode("utf-8")
        )
    except ElementTree.ParseError as exc:
        raise ValueError(f"Not valid XML: {exc}") from exc

    result = GrantorReturn(grantor_ein=normalize_ein(first_text(root, *EIN_FIELDS)))

    year_text = first_text(root, *TAX_YEAR)
    if year_text and year_text[:4].isdigit():
        result.tax_year = int(year_text[:4])

    # The filer's own name comes from the header, where the only business name
    # is its own. Searching the whole document would find a grant recipient's.
    for header in iter_named(root, "ReturnHeader", "ReturnHeaderType"):
        result.grantor_name = _clean_prose(first_text(header, *FILER_NAME))
        break

    for group in iter_named(root, *GRANT_GROUPS):
        recipient_ein = normalize_ein(first_text(group, *GRANT_RECIPIENT_EIN))
        if not recipient_ein:
            continue
        result.grants.append(
            GrantRecord(
                recipient_ein=recipient_ein,
                recipient_name=_clean_prose(first_text(group, *GRANT_RECIPIENT_NAME)),
                cash=first_number(group, *GRANT_CASH),
                non_cash=first_number(group, *GRANT_NON_CASH),
                purpose=_clean_prose(first_text(group, *GRANT_PURPOSE)),
                section=first_text(group, *GRANT_SECTION),
                state=first_text(group, *GRANT_STATE),
            )
        )

    return result


def _parse_programs(root: ElementTree.Element) -> list[ProgramRecord]:
    """Part III program service accomplishments, in the order the filer listed them."""
    programs: list[ProgramRecord] = []
    for group in iter_named(root, *PROGRAM_GROUPS):
        record = ProgramRecord(
            description=_clean_prose(first_text(group, *PROGRAM_DESCRIPTION)),
            expenses=first_number(group, *PROGRAM_EXPENSE),
            grants=first_number(group, *PROGRAM_GRANT),
            revenue=first_number(group, *PROGRAM_REVENUE),
            activity_code=first_text(group, *PROGRAM_ACTIVITY_CODE),
        )
        if not record.is_empty:
            programs.append(record)
    return programs


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

    result.programs = _parse_programs(root)
    result.facts = _parse_facts(root)

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


# Both facts live in the ReturnHeader at the very top of the document, so the
# indexer reads a prefix instead of decompressing whole files. On a full IRS
# bulk download -- hundreds of thousands of documents across several
# multi-hundred-megabyte archives -- this is the difference between a scan that
# finishes while you watch and one you assume has hung.
_PEEK_CHUNK = 64 * 1024
_PEEK_LIMIT = 1024 * 1024
# Enough overlap that a tag split across two chunk boundaries still matches.
_PEEK_OVERLAP = 64


def peek_stream(handle) -> tuple[str | None, int | None]:
    """Filer EIN and tax year, read from the front of an open binary stream."""
    ein: str | None = None
    year: int | None = None
    tail = b""
    consumed = 0

    while consumed < _PEEK_LIMIT:
        chunk = handle.read(_PEEK_CHUNK)
        if not chunk:
            break
        consumed += len(chunk)
        window = tail + chunk
        found_ein, found_year = peek(window)
        ein = ein or found_ein
        year = year or found_year
        if ein is not None and year is not None:
            break
        # Carried over from the whole window, not just the new chunk: a stream
        # that hands back a few bytes at a time would otherwise never hold
        # enough of the document at once to match an element.
        tail = window[-_PEEK_OVERLAP:]

    return ein, year


def _directory_signature(directory: Path) -> list[list]:
    """Cheap fingerprint of a directory, so the index is rebuilt when it changes.

    Hidden files are excluded because the index cache itself is written into
    this directory; including it would invalidate the cache the moment it was
    saved.
    """
    entries: list[list] = []
    for path in directory.iterdir():
        if path.name.startswith("."):
            continue
        if path.is_file():
            entries.append([path.name, path.stat().st_mtime_ns, path.stat().st_size])
        elif path.is_dir() and path.name.endswith(EXPANDED_SUFFIX):
            # An expansion is part of what the index describes, so deleting one
            # has to invalidate the cache that points into it.
            entries.append([path.name, path.stat().st_mtime_ns, -1])
    return sorted(entries)


INDEX_CACHE_FILENAME = ".fqhc-document-index.json"

# Compression methods the standard library cannot decompress. Method 9 is
# Deflate64, which is what the tools used to build very large archives reach for
# once a member or the archive itself crosses the 4 GB mark -- and the IRS bulk
# downloads do. Python raises NotImplementedError on these.
UNSUPPORTED_COMPRESSION = {
    9: "Deflate64",
    1: "Shrink",
    6: "Implode",
    98: "PPMd",
}

_DOCUMENT_INDEX: dict[tuple, dict[str, list[DocumentRef]]] = {}
# Archives the scan could not fully read, per directory scan. Held apart from
# the index so a partial scan still returns everything it did manage to read.
_SCAN_PROBLEMS: dict[tuple, list[str]] = {}

# Where an archive Python cannot read gets expanded to, beside the archive.
EXPANDED_SUFFIX = ".expanded"

# Tools that can decompress Deflate64, best first. ditto and bsdtar both ship
# with macOS; bsdtar is common on Linux. Each entry builds the argument list
# for "expand <archive> into <destination>".
EXPANDERS: tuple[tuple[str, Callable[[Path, Path], list[str]]], ...] = (
    ("ditto", lambda archive, dest: ["ditto", "-x", "-k", str(archive), str(dest)]),
    ("bsdtar", lambda archive, dest: ["bsdtar", "-x", "-f", str(archive), "-C", str(dest)]),
    ("7z", lambda archive, dest: ["7z", "x", "-y", f"-o{dest}", str(archive)]),
)


def find_expander() -> tuple[str, Callable[[Path, Path], list[str]]] | None:
    """The first archive tool on this machine that handles Deflate64."""
    for name, build in EXPANDERS:
        if shutil.which(name):
            return name, build
    return None


def expand_archive(
    archive: Path, *, on_progress: ProgressFn | None = None
) -> Path | None:
    """Unpack an archive Python cannot read, using a system tool.

    Returns the directory holding the expanded files, or None when no suitable
    tool exists. An expansion that is already present is reused rather than
    repeated -- these archives take minutes and gigabytes.
    """
    report = on_progress or (lambda _message: None)
    destination = archive.with_name(archive.name + EXPANDED_SUFFIX)

    if destination.is_dir() and any(destination.rglob("*.xml")):
        return destination

    found = find_expander()
    if found is None:
        return None
    name, build = found

    report(f"Expanding {archive.name} with {name} (Python cannot read it directly)")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            build(archive, destination),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        report(f"Could not run {name}: {exc}")
        return None

    if completed.returncode != 0 and not any(destination.rglob("*.xml")):
        detail = completed.stderr.decode("utf-8", "replace").strip()[:200]
        report(f"{name} failed on {archive.name}: {detail}")
        return None

    return destination


def _load_index_cache(directory: Path, signature: list) -> dict[str, list[DocumentRef]] | None:
    """Read a previously saved index, if it still describes this directory."""
    path = directory / INDEX_CACHE_FILENAME
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if saved.get("version") != 1 or saved.get("signature") != signature:
        return None

    index: dict[str, list[DocumentRef]] = {}
    for ein, refs in (saved.get("documents") or {}).items():
        index[ein] = [
            DocumentRef(
                path=directory / name,
                member=member,
                ein=ein,
                tax_year=year,
            )
            for name, member, year in refs
        ]
    return index


def _save_index_cache(
    directory: Path, signature: list, index: dict[str, list[DocumentRef]]
) -> None:
    payload = {
        "version": 1,
        "signature": signature,
        "documents": {
            ein: [[ref.path.name, ref.member, ref.tax_year] for ref in refs]
            for ein, refs in index.items()
        },
    }
    try:
        (directory / INDEX_CACHE_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError:
        # A read-only download folder is not a reason to fail the run; it only
        # means the next run pays for the scan again.
        pass


def document_index(
    directory: Path,
    *,
    on_progress: ProgressFn | None = None,
    expand: bool = True,
) -> dict[str, list[DocumentRef]]:
    """Map EIN to available documents, newest tax year first.

    Indexes on the EIN *inside* each document rather than on its filename. The
    IRS bulk downloads name files by object id, which contains no EIN at all, so
    filename matching would find nothing in a real download. Zip archives are
    read in place -- there is no need to unpack a multi-gigabyte download.

    The finished index is written to the directory as a small JSON file, so the
    scan is paid for once rather than on every run.
    """
    if not directory.exists():
        return {}

    report = on_progress or (lambda _message: None)
    signature = _directory_signature(directory)
    memory_key = (str(directory), json.dumps(signature))
    cached = _DOCUMENT_INDEX.get(memory_key)
    if cached is not None:
        return cached

    from_disk = _load_index_cache(directory, signature)
    if from_disk is not None:
        _DOCUMENT_INDEX[memory_key] = from_disk
        _SCAN_PROBLEMS[memory_key] = []
        return from_disk

    index: dict[str, list[DocumentRef]] = {}
    problems: list[str] = []
    scanned = 0

    def record(path: Path, member: str | None, handle) -> None:
        nonlocal scanned
        ein, year = peek_stream(handle)
        scanned += 1
        if not ein:
            return
        index.setdefault(ein, []).append(
            DocumentRef(path=path, member=member, ein=ein, tax_year=year)
        )

    def scan_expanded(source: Path, expanded: Path) -> int:
        """Index the loose XML a system tool unpacked for us."""
        count = 0
        for xml in sorted(expanded.rglob("*.xml")):
            try:
                with xml.open("rb") as handle:
                    record(xml, None, handle)
                count += 1
            except OSError:
                continue
        report(f"{source.name}: {count:,} documents recovered from the expansion")
        return count

    def scan_archive(path: Path) -> None:
        unreadable = 0
        method: str | None = None
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.filename.lower().endswith(".xml"):
                    continue
                try:
                    with archive.open(info) as handle:
                        record(path, info.filename, handle)
                except Exception:
                    # One unreadable member must not cost the rest of the
                    # archive, let alone the rest of the run.
                    unreadable += 1
                    method = method or UNSUPPORTED_COMPRESSION.get(
                        info.compress_type, f"method {info.compress_type}"
                    )

        if not unreadable:
            return

        # Python is out of options, but the operating system is not: macOS and
        # most Linux installs ship a tool that reads these. Unpack once and
        # index the result, rather than making this the user's problem.
        if expand:
            expanded = expand_archive(path, on_progress=report)
            if expanded is not None and scan_expanded(path, expanded):
                return

        problems.append(
            f"{path.name}: {unreadable:,} documents could not be read "
            f"({method} compression, which Python cannot decompress)"
        )

    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            # An expansion left by an earlier run, beside the archive it came
            # from. Its archive picks it up; a stray one is indexed here.
            if path.name.endswith(EXPANDED_SUFFIX) and not path.with_name(
                path.name[: -len(EXPANDED_SUFFIX)]
            ).exists():
                scan_expanded(path, path)
            continue
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".xml":
                with path.open("rb") as handle:
                    record(path, None, handle)
            elif suffix == ".zip":
                report(f"Indexing {path.name}")
                scan_archive(path)
                report(f"{path.name}: {scanned:,} documents read so far")
        except Exception as exc:
            # A corrupt or unreadable archive should cost its own contents, not
            # the whole run: the other archives still hold usable filings.
            problems.append(f"{path.name}: could not be opened ({exc})")
            report(f"Skipped {path.name}: {exc}")
            continue

    for refs in index.values():
        refs.sort(key=lambda ref: (ref.tax_year or 0), reverse=True)

    # Only a complete scan is saved. Caching a partial one would make the
    # missing documents look permanently absent once the cause was fixed.
    if index and not problems:
        _save_index_cache(directory, signature, index)
    _DOCUMENT_INDEX[memory_key] = index
    _SCAN_PROBLEMS[memory_key] = problems
    return index


def scan_problems(directory: Path) -> list[str]:
    """Archives the last scan of this directory could not fully read."""
    if not directory.exists():
        return []
    key = (str(directory), json.dumps(_directory_signature(directory)))
    return list(_SCAN_PROBLEMS.get(key, []))


def reset_document_index() -> None:
    """Forget any cached document index. Used by tests."""
    _DOCUMENT_INDEX.clear()
    _SCAN_PROBLEMS.clear()


@dataclass(frozen=True)
class SourceReport:
    """What is actually sitting in the configured IRS directory.

    Every zero-result failure mode of the people stage is distinguishable from
    these numbers, which is why the stage prints them: a missing folder, a
    folder holding the wrong kind of file, an archive that unpacked to nothing,
    and an archive full of documents for other organizations all look identical
    from the outside otherwise.
    """

    directory: Path
    exists: bool
    xml_files: int = 0
    zip_files: int = 0
    other_files: int = 0
    documents: int = 0
    eins: int = 0
    problems: tuple[str, ...] = ()

    @property
    def lines(self) -> list[str]:
        if not self.exists:
            return [f"No IRS XML directory at {self.directory}"]
        if not (self.xml_files or self.zip_files):
            return [
                f"{self.directory} holds no .xml or .zip files "
                f"({self.other_files:,} other files)"
            ]
        return [
            f"{self.directory}: {self.xml_files:,} XML files, "
            f"{self.zip_files:,} archives",
            f"Indexed {self.documents:,} Form 990 documents "
            f"for {self.eins:,} distinct EINs",
        ]

    @property
    def warnings(self) -> list[str]:
        """Archives that could not be read, and what to do about them."""
        if not self.problems:
            return []
        return [
            *self.problems,
            "Fix: `pip install zipfile-deflate64`, then re-run this stage. If "
            "that package will not install, expand those archives in Finder "
            "instead (macOS can read them) and delete the .zip files. Nothing "
            "read from the other archives was lost.",
        ]


def describe_source(
    config, *, on_progress: ProgressFn | None = None
) -> SourceReport:
    """Inspect the configured IRS directory and index it."""
    directory = config.resolve(config.irs.local_directory)
    if not directory.exists():
        return SourceReport(directory=directory, exists=False)

    xml_files = zip_files = other_files = 0
    for path in directory.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        suffix = path.suffix.lower()
        if suffix == ".xml":
            xml_files += 1
        elif suffix == ".zip":
            zip_files += 1
        else:
            other_files += 1

    index = document_index(
        directory,
        on_progress=on_progress,
        expand=config.irs.expand_unreadable_archives,
    )
    return SourceReport(
        directory=directory,
        exists=True,
        xml_files=xml_files,
        zip_files=zip_files,
        other_files=other_files,
        documents=sum(len(refs) for refs in index.values()),
        eins=len(index),
        problems=tuple(scan_problems(directory)),
    )


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


def latest_matching(
    returns: list[Form990Return], predicate: Callable[[Form990Return], bool]
) -> Form990Return | None:
    """The newest return satisfying ``predicate``, or None.

    Sections do not all appear on every return: a filer may describe its
    programs one year and leave Part III thin the next, and a 990-EZ carries
    no Part IX at all. Each section is therefore taken from the newest return
    that actually has it, rather than from whichever return happened to win on
    Part VII -- which would silently discard a program list we hold.
    """
    matching = [r for r in returns if predicate(r)]
    if not matching:
        return None
    return max(matching, key=lambda r: (r.tax_year or 0))


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
    profiles_written: int = 0
    programs_written: int = 0
    source_reachable: bool = True
    messages: list[str] = field(default_factory=list)
    source: "SourceReport | None" = None

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


def _persist_profile(session, ein: str, source: "Form990Return") -> None:
    """Upsert the organization-level facts read off one return."""
    from sqlalchemy import select

    from app.models import FilingProfile, utcnow

    year = source.tax_year or 0
    row = session.scalars(
        select(FilingProfile).where(
            FilingProfile.ein == ein, FilingProfile.tax_year == year
        )
    ).first()
    if row is None:
        row = FilingProfile(ein=ein, tax_year=year)
        session.add(row)

    facts = source.facts
    for name in (
        "mission",
        "formation_year",
        "domicile_state",
        "website",
        "employee_count",
        "volunteer_count",
        "total_revenue",
        "total_expenses",
        "total_assets",
        "total_liabilities",
        "net_assets",
        "program_expenses",
        "management_expenses",
        "fundraising_expenses",
        "grants_paid",
        "salaries",
        "financials_audited",
        "single_audit_required",
        "single_audit_performed",
        "audit_committee",
    ):
        setattr(row, name, getattr(facts, name))
    row.fetched_at = utcnow()
    session.flush()


def _persist_programs(session, ein: str, source: "Form990Return") -> int:
    """Replace this EIN's program areas with those from ``source``."""
    from sqlalchemy import select

    from app.models import ProgramArea

    year = source.tax_year or 0

    # Programs are positional -- Part III lists the largest first -- so they are
    # replaced as a set rather than matched row by row. Every year is cleared,
    # not just this one: the page shows one year's programs, and leaving an
    # older year behind would put two vintages of the same program on screen.
    for row in session.scalars(
        select(ProgramArea).where(ProgramArea.ein == ein)
    ).all():
        session.delete(row)
    session.flush()

    for position, program in enumerate(source.programs):
        session.add(
            ProgramArea(
                ein=ein,
                tax_year=year,
                position=position,
                description=program.description,
                expenses=program.expenses,
                grants=program.grants,
                revenue=program.revenue,
                activity_code=program.activity_code,
            )
        )
    session.flush()
    return len(source.programs)


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
        FilingProfile,
        IngestRun,
        MatchStatus,
        Organization,
        Person,
        ProgramArea,
        RunStatus,
        utcnow,
    )

    report = on_progress or (lambda _message: None)
    result = PeopleResult()

    run = IngestRun(stage="people", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    try:
        source = describe_source(config, on_progress=report)
        for line in source.lines:
            report(line)
        result.source = source
        result.messages.extend(source.warnings)

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

        if not result.eligible:
            # By far the most common way this stage produces nothing: it was run
            # on its own, before anything had populated the EINs it reads.
            result.messages.append(
                "No organization has a confirmed EIN yet, so there was nothing "
                "to look up. Run `python -m pipeline.run --stage hrsa` then "
                "`--stage ein` first, or `python -m pipeline.run` for the "
                "whole pipeline."
            )

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
            # Each section comes from the newest return that carries it. A
            # filer can describe its programs one year and leave Part III thin
            # the next, and a return with no Part VII at all still tells us the
            # organization's mission, age, headcount and balance sheet.
            facts_source = latest_matching(parsed, lambda r: r.facts.has_any)
            programs_source = latest_matching(parsed, lambda r: bool(r.programs))

            if chosen is None and facts_source is None and programs_source is None:
                result.without_documents += 1
                continue

            result.resolved += 1

            if chosen is not None:
                year = chosen.tax_year or 0

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

            if facts_source is not None:
                _persist_profile(session, ein, facts_source)
                result.profiles_written += 1

            if programs_source is not None:
                result.programs_written += _persist_programs(
                    session, ein, programs_source
                )

        session.commit()
    except Exception as exc:
        session.rollback()
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.message = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise

    if result.without_documents:
        source = result.source
        if source is not None and not source.exists:
            detail = (
                f"there is no directory at {source.directory} -- create it and "
                "unzip an IRS Form 990 download into it"
            )
        elif source is not None and not (source.xml_files or source.zip_files):
            detail = (
                f"{source.directory} contains no .xml or .zip files -- the IRS "
                "download needs to be copied there, not left in ~/Downloads"
            )
        elif source is not None and source.documents:
            detail = (
                f"the {source.documents:,} documents in {source.directory} "
                f"cover {source.eins:,} other EINs -- these organizations filed "
                "in a year you have not downloaded"
            )
        else:
            detail = "see irs.local_directory in config.yaml"
        result.messages.append(
            f"No Form 990 XML found for {result.without_documents:,} of "
            f"{result.eligible:,} organizations: {detail}"
        )

    report(
        f"Stored {result.people_written:,} people and "
        f"{result.contractors_written:,} contractors for {result.resolved:,} "
        "organizations"
    )
    if result.profiles_written or result.programs_written:
        report(
            f"Read {result.profiles_written:,} organization profiles and "
            f"{result.programs_written:,} program areas off the same returns"
        )

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.eligible
    run.records_written = result.people_written + result.contractors_written
    run.source_reachable = result.source_reachable
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result
