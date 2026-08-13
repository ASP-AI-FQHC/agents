"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Config, load_config
from app.db import create_db_engine
from app.models import Base

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def config() -> Config:
    """The real project config, so tests exercise the shipped defaults."""
    return load_config(REPO_ROOT / "config.yaml")


@pytest.fixture()
def session(tmp_path: Path) -> Session:
    """An isolated on-disk SQLite database per test."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()
