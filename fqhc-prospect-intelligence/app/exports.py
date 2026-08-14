"""CSV and XLSX exports of the current filtered view.

Both formats open with a header block naming the company, the report, when it
was generated and which filters produced it, so a file that has been emailed
around still says what it is and how current it was.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.config import Config
from app.formatting import NOT_AVAILABLE
from app.queries import DataStatus, Filters, ProspectRow
from pipeline.propublica import format_ein

COLUMNS: tuple[tuple[str, str], ...] = (
    ("ICP score", "score"),
    ("Organization", "name"),
    ("City", "city"),
    ("State", "state"),
    ("ZIP", "zip"),
    ("Delivery sites", "sites"),
    ("Grantee type", "grantee_type"),
    ("EIN", "ein"),
    ("EIN match status", "match_status"),
    ("EIN match confidence", "match_score"),
    ("Total revenue", "revenue"),
    ("Revenue tax year", "revenue_year"),
    ("Federal award", "award"),
    ("Phone", "phone"),
    ("Website", "website"),
)

# Brand colors, as openpyxl wants them (ARGB, no leading #).
BRAND_BLUE = "FF0094BB"
BRAND_GREEN = "FF6FC055"
BRAND_GRAY = "FF999999"


def _cell_values(row: ProspectRow) -> dict[str, object]:
    """Raw values for one row. Unknowns stay None until formatting."""
    organization = row.organization
    match = row.match
    return {
        "score": row.composite,
        "name": organization.name,
        "city": organization.city,
        "state": organization.state,
        "zip": organization.zip_code,
        "sites": organization.site_count,
        "grantee_type": getattr(organization.grantee_type, "value", organization.grantee_type),
        "ein": format_ein(row.ein),
        "match_status": getattr(match.status, "value", match.status) if match else "unmatched",
        "match_score": match.score if match else None,
        "revenue": row.revenue,
        "revenue_year": row.revenue_year,
        "award": organization.federal_award_amount,
        "phone": organization.phone,
        "website": organization.website,
    }


def _header_block(
    config: Config,
    filters: Filters,
    row_count: int,
    status: DataStatus | None,
    generated_at: datetime | None = None,
) -> list[list[str]]:
    """The provenance block that opens every export."""
    stamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    block = [
        [config.app.company],
        [config.app.name],
        [f"Generated {stamp}"],
        [f"Filters: {filters.describe()}"],
        [f"Rows: {row_count:,}"],
    ]

    # If the underlying data came from cache, say so in the file itself -- an
    # exported spreadsheet outlives the banner on the screen.
    if status is not None:
        if status.on_cached_data and status.cache_date:
            block.append(
                [
                    "Source data: cached copy from "
                    f"{status.cache_date:%Y-%m-%d} (source was unreachable)"
                ]
            )
        elif status.latest_run and status.latest_run.finished_at:
            block.append(
                [f"Source data refreshed {status.latest_run.finished_at:%Y-%m-%d}"]
            )
    block.append([])
    return block


def to_csv(
    rows: list[ProspectRow],
    config: Config,
    filters: Filters,
    status: DataStatus | None = None,
    generated_at: datetime | None = None,
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    for line in _header_block(config, filters, len(rows), status, generated_at):
        writer.writerow(line)

    writer.writerow([label for label, _ in COLUMNS])
    for row in rows:
        values = _cell_values(row)
        writer.writerow(
            [
                # Missing data is written as the same words the UI shows, so a
                # blank cell never gets read as a zero.
                NOT_AVAILABLE if values[key] is None else values[key]
                for _, key in COLUMNS
            ]
        )
    return buffer.getvalue()


def to_xlsx(
    rows: list[ProspectRow],
    config: Config,
    filters: Filters,
    status: DataStatus | None = None,
    generated_at: datetime | None = None,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Prospects"

    header_block = _header_block(config, filters, len(rows), status, generated_at)
    for index, line in enumerate(header_block, start=1):
        sheet.cell(row=index, column=1, value=line[0] if line else None)

    sheet.cell(row=1, column=1).font = Font(name="Open Sans", size=14, bold=True, color=BRAND_BLUE)
    sheet.cell(row=2, column=1).font = Font(name="Open Sans", size=11, bold=True)
    for index in range(3, len(header_block) + 1):
        sheet.cell(row=index, column=1).font = Font(name="Open Sans", size=9, color=BRAND_GRAY)

    header_row = len(header_block) + 1
    thin = Side(style="thin", color="FFE4E8EA")

    for column_index, (label, _) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=header_row, column=column_index, value=label)
        cell.font = Font(name="Open Sans", size=10, bold=True, color="FFFFFFFF")
        cell.fill = PatternFill("solid", fgColor=BRAND_GREEN)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = Border(bottom=thin)

    for offset, row in enumerate(rows, start=1):
        values = _cell_values(row)
        for column_index, (_, key) in enumerate(COLUMNS, start=1):
            value = values[key]
            cell = sheet.cell(
                row=header_row + offset,
                column=column_index,
                value=NOT_AVAILABLE if value is None else value,
            )
            cell.font = Font(name="Open Sans", size=10)
            if value is None:
                cell.font = Font(name="Open Sans", size=10, italic=True, color=BRAND_GRAY)
            elif key in ("revenue", "award"):
                cell.number_format = '"$"#,##0'
            elif key in ("score", "match_score"):
                cell.number_format = "0.0"

    widths = {
        "name": 42,
        "city": 18,
        "website": 32,
        "match_status": 16,
        "grantee_type": 14,
        "revenue": 16,
        "award": 16,
        "phone": 16,
    }
    for column_index, (label, key) in enumerate(COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(column_index)].width = widths.get(
            key, max(len(label) + 2, 12)
        )

    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def export_filename(extension: str, generated_at: datetime | None = None) -> str:
    stamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return f"allstar-fqhc-prospects-{stamp}.{extension}"
