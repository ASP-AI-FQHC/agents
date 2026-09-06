"""Audit a third-party FQHC spreadsheet before any of it reaches the database.

    python -m pipeline.review_import ~/Downloads/FQHC_Filtered_Data.csv --state IL

Spreadsheets of health centers with CEO names and email addresses circulate
widely, and some of them are largely generated. This database is worth having
because every figure in it can be traced to a filing, so a file gets audited
before it is trusted, not after.

**Nothing here writes to the database.** It reads a file, matches each row
against the organizations already known -- which answers the duplicate question
directly -- and reports what the file claims, what agrees with the filings, and
what does not.

The audit looks for the specific ways this kind of file goes wrong:

* an email constructed from the organization's name rather than published by it
* a placeholder where a person's name should be
* a contact whose address belongs to somebody else entirely
* a numeric column that is uniformly distributed, which no real-world count is
* figures that contradict the organization's own Form 990

None of these is proof on its own. Together they are the difference between a
file worth importing and a file worth deleting, and the report shows the
workings so the decision stays with a person.
"""

from __future__ import annotations

import argparse
import collections
import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.hrsa import FieldSpec, resolve_columns
from pipeline.text import clean, normalize_name, normalize_state, parse_int, parse_money

# Tolerant, like every other reader here: the column names differ between
# whoever assembled the file and whoever exported it.
IMPORT_FIELDS: dict[str, FieldSpec] = {
    "name": FieldSpec(
        aliases=("Name", "Organization Name", "Health Center Name", "Organization"),
        contains=(("organization", "name"), ("health", "center", "name")),
        exclude=("ceo", "contact", "director", "site"),
    ),
    "website": FieldSpec(aliases=("Website", "URL", "Web"), contains=(("website",),)),
    "phone": FieldSpec(aliases=("Phone", "Telephone", "Main Phone"), contains=(("phone",),)),
    "email": FieldSpec(
        aliases=("Email", "General Email", "Contact Email"),
        contains=(("email",),),
        exclude=("ceo", "director", "executive"),
    ),
    "street": FieldSpec(aliases=("Address", "Street", "Street Address"), contains=(("address",),)),
    "city": FieldSpec(aliases=("City",), contains=(("city",),)),
    "state": FieldSpec(aliases=("State", "ST"), contains=(("state",),)),
    "zip": FieldSpec(aliases=("ZIP", "Zip Code", "Postal Code"), contains=(("zip",), ("postal",))),
    "ceo_name": FieldSpec(
        aliases=("CEO Name", "CEO", "Executive Director", "Chief Executive"),
        contains=(("ceo", "name"), ("chief", "executive")),
        exclude=("email",),
    ),
    "ceo_email": FieldSpec(
        aliases=("CEO Email", "Executive Email"),
        contains=(("ceo", "email"), ("executive", "email")),
    ),
    "employees": FieldSpec(aliases=("Employees", "Staff", "Headcount"), contains=(("employee",),)),
    "clinics": FieldSpec(aliases=("Clinics", "Sites", "Locations"), contains=(("clinic",), ("site",))),
    "revenue": FieldSpec(
        aliases=("Annual Revenue", "Revenue", "Total Revenue"),
        contains=(("revenue",),),
    ),
    "founded": FieldSpec(
        aliases=("Founded Year", "Year Founded", "Founded", "Year Formed"),
        contains=(("founded",), ("year", "formed")),
    ),
    "source": FieldSpec(aliases=("Data Source", "Source"), contains=(("source",),)),
    "verified": FieldSpec(
        aliases=("Last Verified", "Verified", "Last Updated"),
        contains=(("verified",), ("last", "updated")),
    ),
}

# Names that are not names.
PLACEHOLDER_NAMES = frozenset(
    {
        "contact organization", "contact us", "n/a", "na", "none", "unknown",
        "not available", "tbd", "ceo", "chief executive officer", "executive director",
        "administrator", "info", "management", "leadership", "-", "--",
    }
)

_EMAIL = re.compile(r"^[^@\s]+@([^@\s]+)$")


def _domain(value: str | None) -> str:
    match = _EMAIL.match((value or "").strip())
    return match.group(1).lower() if match else ""


def _site_domain(value: str | None) -> str:
    text = (value or "").strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    if text.startswith("www."):
        text = text[4:]
    return text.split("/")[0].strip()


def _stem(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def looks_constructed(email: str | None, organization_name: str | None) -> bool:
    """Whether an address was built from the organization's name.

    The tell is a generic local part on a domain that is the organization's
    name with the end cut off: "Howard Brown Health Center" becomes
    ``info@howardbrownheal.org``, which is not a domain that exists. A real
    generic mailbox sits on the organization's real domain, so the check is
    that the domain is a *truncated prefix* of the name rather than the name.
    """
    domain = _domain(email)
    if not domain or not organization_name:
        return False

    local = (email or "").split("@")[0].lower()
    if local not in {"info", "contact", "admin", "hello", "office", "mail"}:
        return False

    root = _stem(domain.rsplit(".", 1)[0])
    stem = _stem(organization_name)
    if not root or root == stem or not stem.startswith(root):
        return False

    # A shortened domain is normal and legitimate: "Howard Brown Health Center"
    # really does publish howardbrown.org. What is not legitimate is a cut in
    # the middle of a word -- howardbrownHEAL from "health", belovedcommunIT
    # from "community" -- which is a string truncated to a fixed width rather
    # than a name shortened by someone who knew where the words were.
    words = [w for w in re.split(r"[^a-z0-9]+", organization_name.lower()) if w]
    boundary = ""
    for word in words:
        boundary += word
        if boundary == root:
            return False   # stops exactly at a word: a real abbreviation
        if len(boundary) > len(root):
            break
    return True


def name_matches_email(person: str | None, email: str | None) -> bool | None:
    """Whether an address plausibly belongs to the person named beside it.

    None when there is not enough to judge. This is deliberately generous --
    initials, first-initial-plus-surname and full names all count -- so a False
    means the address contains no trace of the person's name at all.
    """
    local = (email or "").split("@")[0].lower()
    if not local or not (person or "").strip():
        return None
    parts = [p for p in re.split(r"[^a-z]+", person.lower()) if len(p) > 1]
    if not parts:
        return None

    if any(part in local for part in parts):
        return True
    first, last = parts[0], parts[-1]
    return local.startswith(first[0] + last[:3]) or local.startswith(last[:3] + first[0])


def flatness(values: list[int]) -> tuple[float, int] | None:
    """How evenly the common values share the mass, and how many share it.

    Real-world counts of anything -- clinics, sites, employees -- are skewed:
    many organizations have three sites, very few have twenty. A column where
    a dozen different values are each about as common as the others was drawn
    from a random number generator.

    Measured on the values that carry the bulk of the rows rather than on the
    whole column, because a handful of rare outliers otherwise disguises a
    perfectly flat middle -- which is exactly what happened the first time this
    was written: a column of 118, 118, 117, 117, 116, 116 ... scored 0.87 on
    normalized entropy and slipped through.

    Returns ``(ratio, count)`` where ratio is the largest of those counts over
    the smallest -- 1.0 is perfectly flat -- or None when there is too little
    to judge.
    """
    counts = collections.Counter(values)
    if len(counts) < 5 or len(values) < 50:
        return None

    ordered = [n for _value, n in counts.most_common()]
    bulk: list[int] = []
    running = 0
    for n in ordered:
        bulk.append(n)
        running += n
        if running >= len(values) * 0.8:
            break

    if len(bulk) < 6:
        # A few values carry most of the rows, which is what a real count of
        # anything looks like.
        return None
    return max(bulk) / min(bulk), len(bulk)


@dataclass
class RowReview:
    """One spreadsheet row, matched and audited."""

    line: int
    name: str
    state: str | None = None
    matched_id: int | None = None
    matched_name: str | None = None
    duplicate_of_line: int | None = None
    problems: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.duplicate_of_line is not None:
            return "duplicate in file"
        return "already in database" if self.matched_id else "new"


@dataclass
class ImportReview:
    path: Path
    rows: list[RowReview] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)
    missing_columns: list[str] = field(default_factory=list)
    column_findings: list[str] = field(default_factory=list)
    total: int = 0

    def count(self, status: str) -> int:
        return sum(1 for row in self.rows if row.status == status)

    @property
    def trustworthy(self) -> list[RowReview]:
        return [row for row in self.rows if not row.problems and not row.conflicts]


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


def review(path: Path, session=None, state: str | None = None) -> ImportReview:
    """Audit a file and match it against what the database already holds."""
    from app.models import Organization

    headers, raw = read_rows(path)
    result = ImportReview(path=path, total=len(raw))
    columns = resolve_columns(headers, IMPORT_FIELDS)
    result.columns = columns
    result.missing_columns = [key for key in ("name",) if key not in columns]
    if result.missing_columns:
        return result

    def value(row: dict[str, str], key: str) -> str | None:
        column = columns.get(key)
        return clean(row.get(column)) if column else None

    # --- Column-level findings, which are about the file as a whole ---------
    for key, label in (("clinics", "Clinics"), ("employees", "Employees")):
        if key not in columns:
            continue
        numbers = [
            n for n in (parse_int(value(row, key)) for row in raw) if n is not None
        ]
        measured = flatness(numbers)
        if measured is not None and measured[0] < 1.3:
            ratio, spread = measured
            result.column_findings.append(
                f"{label}: {spread} different values each account for about the "
                f"same number of rows (the most common is only {ratio:.2f}x the "
                "least). No real count of anything is spread evenly like that; "
                "this column was generated."
            )

        zeros = sum(1 for n in numbers if n == 0)
        if numbers and zeros > len(numbers) * 0.05:
            result.column_findings.append(
                f"{label}: {zeros:,} rows report zero. A health center with no "
                f"{label.lower()} is not a health center."
            )

    if "revenue" in columns:
        amounts = [
            a for a in (parse_money(value(row, "revenue")) for row in raw) if a
        ]
        if amounts:
            round_ones = sum(1 for a in amounts if a % 100_000 == 0)
            share = round_ones / len(amounts)
            distinct = len(set(amounts))
            if share > 0.5 or distinct < len(amounts) / 3:
                result.column_findings.append(
                    f"Annual Revenue: {round_ones:,} of {len(amounts):,} are round "
                    f"to $100,000, and there are only {distinct:,} distinct values "
                    f"across {len(amounts):,} rows. Reported revenue is not round."
                )

    if "founded" in columns:
        years = collections.Counter(
            value(row, "founded") for row in raw if value(row, "founded")
        )
        if years:
            year, count = years.most_common(1)[0]
            # A share alone is meaningless on a short file, where one row is
            # half of it.
            if count >= 20 and count > len(raw) * 0.1:
                result.column_findings.append(
                    f"Founded Year: {count:,} rows say exactly {year}. That is a "
                    "default, not a finding."
                )

    # --- Match against the database ----------------------------------------
    by_name_state: dict[tuple[str, str], Organization] = {}
    ambiguous: set[tuple[str, str]] = set()
    if session is not None:
        from sqlalchemy import select

        for organization in session.scalars(select(Organization)).all():
            key = (organization.normalized_name, organization.state or "")
            if key in by_name_state:
                ambiguous.add(key)
            else:
                by_name_state[key] = organization

    seen: dict[str, int] = {}

    for line, row in enumerate(raw, start=2):
        name = value(row, "name") or ""
        row_state = normalize_state(value(row, "state"))
        if state and row_state and row_state != state.upper():
            continue
        entry = RowReview(line=line, name=name, state=row_state)

        # Duplicates inside the file itself, which no import should create.
        stem = _stem(name)
        if stem and stem in seen:
            entry.duplicate_of_line = seen[stem]
        elif stem:
            seen[stem] = line

        normalized = normalize_name(name)
        key = (normalized, row_state or "")
        if key in by_name_state and key not in ambiguous:
            organization = by_name_state[key]
            entry.matched_id = organization.id
            entry.matched_name = organization.name
        elif not row_state:
            # Without a state a name match is a guess, so the row is reported
            # as new rather than quietly attached to somebody.
            candidates = [
                organization
                for (candidate_name, _s), organization in by_name_state.items()
                if candidate_name == normalized
            ]
            if len(candidates) == 1:
                entry.matched_id = candidates[0].id
                entry.matched_name = candidates[0].name

        # --- Row-level audit ------------------------------------------------
        ceo = value(row, "ceo_name")
        if ceo and ceo.strip().lower() in PLACEHOLDER_NAMES:
            entry.problems.append(f"CEO Name is the placeholder {ceo!r}")

        general = value(row, "email")
        if looks_constructed(general, name):
            entry.problems.append(
                f"Email {general!r} is the organization's name truncated, not an "
                "address it published"
            )

        ceo_email = value(row, "ceo_email")
        if ceo_email and ceo and ceo.strip().lower() not in PLACEHOLDER_NAMES:
            if name_matches_email(ceo, ceo_email) is False:
                entry.problems.append(
                    f"CEO Email {ceo_email!r} contains no part of {ceo!r} -- it "
                    "appears to belong to somebody else"
                )

        website = value(row, "website")
        if website and ceo_email:
            site, mail = _site_domain(website), _domain(ceo_email)
            if site and mail and site != mail and not (
                site.endswith(mail) or mail.endswith(site)
            ):
                entry.problems.append(
                    f"CEO Email is on {mail}, but the website is {site}"
                )

        # --- Against the organization's own filings ---------------------------
        if entry.matched_id and session is not None:
            entry.conflicts.extend(_conflicts(session, entry.matched_id, row, value))

        result.rows.append(entry)

    return result


def _conflicts(session, organization_id: int, row, value) -> list[str]:
    """Where the file contradicts what the organization filed itself."""
    from sqlalchemy import select

    from app.models import Filing, Organization

    out: list[str] = []
    organization = session.get(Organization, organization_id)
    if organization is None:
        return out

    claimed = parse_money(value(row, "revenue"))
    if claimed and organization.ein:
        filing = session.scalars(
            select(Filing)
            .where(Filing.ein == organization.ein, Filing.total_revenue.is_not(None))
            .order_by(Filing.tax_year.desc())
            .limit(1)
        ).first()
        if filing and filing.total_revenue:
            ratio = claimed / filing.total_revenue
            if ratio > 1.5 or ratio < 0.67:
                out.append(
                    f"Revenue ${claimed:,.0f} against ${filing.total_revenue:,.0f} "
                    f"on the FY{filing.tax_year} Form 990 ({ratio:.1f}x)"
                )

    claimed_sites = parse_int(value(row, "clinics"))
    if claimed_sites and organization.site_count:
        if abs(claimed_sites - organization.site_count) > max(
            3, organization.site_count
        ):
            out.append(
                f"{claimed_sites} clinics against {organization.site_count} "
                "delivery sites published by HRSA"
            )

    return out


def render(result: ImportReview, *, examples: int = 8) -> str:
    lines = [
        f"Import review: {result.path.name}",
        "=" * 68,
        f"  Rows in file                 {result.total:>6,}",
    ]
    if result.missing_columns:
        lines.append("")
        lines.append(
            "  No organization-name column found, so nothing can be matched. "
            f"Columns present: {', '.join(result.columns) or 'none recognised'}"
        )
        return "\n".join(lines)

    lines += [
        f"  Reviewed                     {len(result.rows):>6,}",
        f"  Already in the database      {result.count('already in database'):>6,}",
        f"  Duplicated inside the file   {result.count('duplicate in file'):>6,}",
        f"  Not currently known          {result.count('new'):>6,}",
        f"  With no problem found        {len(result.trustworthy):>6,}",
        "",
    ]

    if result.column_findings:
        lines.append("Whole-column findings")
        lines.append("-" * 68)
        for finding in result.column_findings:
            lines.append(f"  * {finding}")
        lines.append("")

    problems = collections.Counter(
        problem.split(" -- ")[0].split(" '")[0].split(" is ")[0]
        for row in result.rows
        for problem in row.problems
    )
    if problems:
        lines.append("Row problems, by kind")
        lines.append("-" * 68)
        for kind, count in problems.most_common():
            lines.append(f"  {count:>6,}  {kind}")
        lines.append("")

    flagged = [row for row in result.rows if row.problems or row.conflicts]
    if flagged:
        lines.append(f"Examples ({min(examples, len(flagged))} of {len(flagged):,})")
        lines.append("-" * 68)
        for row in flagged[:examples]:
            lines.append(f"  line {row.line}: {row.name}  [{row.status}]")
            for problem in row.problems:
                lines.append(f"      ! {problem}")
            for conflict in row.conflicts:
                lines.append(f"      ~ {conflict}")
        lines.append("")

    lines.append(
        "Nothing has been written to the database. This report is a reading of "
        "the file, not a change to anything."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.review_import",
        description="Audit a third-party FQHC spreadsheet. Writes nothing.",
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--state", help="Review only rows in this state")
    parser.add_argument("--examples", type=int, default=8)
    parser.add_argument("--config")
    arguments = parser.parse_args(argv)

    if not arguments.path.exists():
        print(f"{arguments.path} does not exist.")
        return 1

    from app.config import get_config, load_config
    from app.db import session_scope

    config = load_config(arguments.config) if arguments.config else get_config()
    with session_scope(config) as session:
        result = review(arguments.path, session, state=arguments.state)
        print(render(result, examples=arguments.examples))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
