"""Database engine and session management.

A single SQLite file, created on demand. ``init_db`` is idempotent, so both the
CLI pipeline and the web app can call it at startup without coordination.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Config, get_config
from app.models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """Enable foreign keys (off by default in SQLite) and WAL for concurrency."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def create_db_engine(database_url: str) -> Engine:
    engine = create_engine(
        database_url,
        future=True,
        # The pipeline can run in a background thread of the web app.
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def get_engine(config: Config | None = None) -> Engine:
    global _engine
    if _engine is None:
        config = config or get_config()
        db_file: Path = config.database_file
        db_file.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_db_engine(config.database_url)
    return _engine


def get_session_factory(config: Config | None = None) -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(config), expire_on_commit=False, future=True
        )
    return _SessionFactory


def init_db(config: Config | None = None) -> Engine:
    """Create any missing tables. Safe to call repeatedly."""
    engine = get_engine(config)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(config: Config | None = None) -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on error."""
    session = get_session_factory(config)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a read-oriented session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine/session factory. Used by tests."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
