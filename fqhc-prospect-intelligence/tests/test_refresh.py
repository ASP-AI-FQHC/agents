"""The background refresh manager behind the dashboard's Refresh button."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.config import Config
from app.db import init_db, reset_engine
from app.models import RunStatus
from app.refresh import RefreshManager


@dataclass(frozen=True)
class FakeStage:
    name: str
    description: str
    run: object


@pytest.fixture()
def isolated_config(config: Config, tmp_path: Path) -> Config:
    """Point the module-level engine at a throwaway database."""
    config.app.database_path = tmp_path / "refresh.db"
    reset_engine()
    init_db(config)
    yield config
    reset_engine()


def make_stage(name: str, outcome=None, raises: Exception | None = None) -> FakeStage:
    def run(_session, _config, _force, report):
        report(f"{name} working")
        if raises:
            raise raises
        return outcome

    return FakeStage(name=name, description=f"Run {name}", run=run)


class Result:
    def __init__(self, status=RunStatus.SUCCESS, messages=None):
        self.status = status
        self.messages = messages or []


def test_run_completes_and_reports_progress(isolated_config, monkeypatch) -> None:
    manager = RefreshManager()
    monkeypatch.setattr(
        "app.refresh.STAGES",
        (make_stage("hrsa", Result()), make_stage("scoring", Result())),
    )

    assert manager.start(isolated_config) is True
    manager.join(timeout=10)
    state = manager.state

    assert state.running is False
    assert state.finished_at is not None
    assert state.percent == 100
    assert "hrsa working" in state.messages
    assert "scoring working" in state.messages
    assert state.error is None


def test_only_one_run_at_a_time(isolated_config, monkeypatch) -> None:
    import threading

    gate = threading.Event()

    def blocking(_session, _config, _force, report):
        report("waiting")
        gate.wait(timeout=5)
        return Result()

    manager = RefreshManager()
    monkeypatch.setattr(
        "app.refresh.STAGES",
        (FakeStage(name="slow", description="Slow stage", run=blocking),),
    )

    assert manager.start(isolated_config) is True
    assert manager.is_running is True
    # A second click must not launch a concurrent pipeline.
    assert manager.start(isolated_config) is False

    gate.set()
    manager.join(timeout=10)
    assert manager.is_running is False


def test_a_failing_stage_does_not_abandon_the_rest(isolated_config, monkeypatch) -> None:
    """HRSA data is still worth having when ProPublica is unreachable."""
    manager = RefreshManager()
    monkeypatch.setattr(
        "app.refresh.STAGES",
        (
            make_stage("ein", raises=RuntimeError("ProPublica unreachable")),
            make_stage("scoring", Result()),
        ),
    )

    manager.start(isolated_config)
    manager.join(timeout=10)
    state = manager.state

    assert any("ein failed" in warning for warning in state.warnings)
    assert "scoring working" in state.messages
    assert state.percent == 100


def test_partial_stage_is_flagged_as_cache_backed(isolated_config, monkeypatch) -> None:
    manager = RefreshManager()
    monkeypatch.setattr(
        "app.refresh.STAGES",
        (make_stage("hrsa", Result(status=RunStatus.PARTIAL, messages=["cache used"])),),
    )

    manager.start(isolated_config)
    manager.join(timeout=10)
    warnings = manager.state.warnings

    assert "cache used" in warnings
    assert any("completed on cached data" in warning for warning in warnings)


def test_state_serializes_for_the_status_endpoint(isolated_config, monkeypatch) -> None:
    manager = RefreshManager()
    monkeypatch.setattr("app.refresh.STAGES", (make_stage("hrsa", Result()),))

    manager.start(isolated_config)
    manager.join(timeout=10)
    payload = manager.state.as_dict()

    assert set(payload) >= {
        "running",
        "stage",
        "percent",
        "messages",
        "warnings",
        "started_at",
        "finished_at",
        "error",
    }
    assert payload["running"] is False
    assert payload["percent"] == 100
