"""The additive migration shim that keeps existing databases usable."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.db import apply_additive_migrations, create_db_engine
from app.models import Base, Filing


def test_new_nullable_column_is_added_in_place(tmp_path: Path) -> None:
    """A database built before period_end existed must keep working."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE filings DROP COLUMN period_end"))

    assert "period_end" not in {
        column["name"] for column in inspect(engine).get_columns("filings")
    }

    applied = apply_additive_migrations(engine)

    assert "filings.period_end" in applied
    assert "period_end" in {
        column["name"] for column in inspect(engine).get_columns("filings")
    }
    engine.dispose()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'current.db'}")
    Base.metadata.create_all(engine)

    assert apply_additive_migrations(engine) == []
    assert apply_additive_migrations(engine) == []
    engine.dispose()


def test_existing_rows_survive_the_migration(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'data.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO filings (ein, tax_year, total_revenue, fetched_at) "
                "VALUES ('362167869', 2023, 20000000, '2026-01-01 00:00:00')"
            )
        )
        connection.execute(text("ALTER TABLE filings DROP COLUMN period_end"))

    apply_additive_migrations(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT total_revenue, period_end FROM filings")
        ).one()
    assert row.total_revenue == 20_000_000
    assert row.period_end is None
    engine.dispose()


def test_missing_non_nullable_column_raises_with_instructions(tmp_path: Path) -> None:
    """A change this shim cannot handle must say so, not fail cryptically later."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'broken.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE organizations DROP COLUMN site_count"))

    with pytest.raises(RuntimeError, match="Delete the database file"):
        apply_additive_migrations(engine)
    engine.dispose()
