"""Background pipeline runs for the dashboard's "Refresh Data" button.

One run at a time, in a worker thread, with progress the UI can poll. The
pipeline is otherwise identical to ``python -m pipeline.run`` -- this module
only wraps it in a thread and records what it is doing.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import Config
from app.db import session_scope
from app.models import RunStatus, utcnow
from pipeline.run import STAGES


@dataclass
class RefreshState:
    """Snapshot of a pipeline run, serialized straight to the status endpoint."""

    running: bool = False
    stage: str | None = None
    stage_index: int = 0
    stage_count: int = 0
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    force_refresh: bool = False

    @property
    def percent(self) -> int:
        if not self.stage_count:
            return 0
        if not self.running and self.finished_at:
            return 100
        return int(min(self.stage_index / self.stage_count, 1.0) * 100)

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "stage": self.stage,
            "percent": self.percent,
            "messages": self.messages[-40:],
            "warnings": self.warnings,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }


class RefreshManager:
    """Serializes pipeline runs triggered from the UI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RefreshState()
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> RefreshState:
        with self._lock:
            # Copy so a caller rendering the template cannot observe a
            # half-updated snapshot.
            return RefreshState(
                running=self._state.running,
                stage=self._state.stage,
                stage_index=self._state.stage_index,
                stage_count=self._state.stage_count,
                messages=list(self._state.messages),
                warnings=list(self._state.warnings),
                started_at=self._state.started_at,
                finished_at=self._state.finished_at,
                error=self._state.error,
                force_refresh=self._state.force_refresh,
            )

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state.running

    def start(
        self,
        config: Config,
        *,
        force_refresh: bool = False,
        stages: Iterable[str] | None = None,
    ) -> bool:
        """Begin a run. Returns False if one is already in progress."""
        with self._lock:
            if self._state.running:
                return False
            self._state = RefreshState(
                running=True,
                stage_count=len(list(stages)) if stages else len(STAGES),
                started_at=utcnow(),
                force_refresh=force_refresh,
            )

        selected = [s for s in STAGES if not stages or s.name in stages]
        self._thread = threading.Thread(
            target=self._run,
            args=(config, selected, force_refresh),
            name="fqhc-refresh",
            daemon=True,
        )
        self._thread.start()
        return True

    def join(self, timeout: float | None = None) -> None:
        """Wait for the current run. Used by tests."""
        if self._thread is not None:
            self._thread.join(timeout)

    # -- worker --------------------------------------------------------------

    def _record(self, message: str) -> None:
        with self._lock:
            self._state.messages.append(message)

    def _warn(self, message: str) -> None:
        with self._lock:
            self._state.warnings.append(message)

    def _run(self, config: Config, stages: list[Any], force_refresh: bool) -> None:
        try:
            with session_scope(config) as session:
                for index, stage in enumerate(stages):
                    with self._lock:
                        self._state.stage = stage.description
                        self._state.stage_index = index
                    self._record(f"{stage.description}...")

                    try:
                        result = stage.run(session, config, force_refresh, self._record)
                    except Exception as exc:
                        # One stage failing should not abandon the others: HRSA
                        # data is still useful when ProPublica is unreachable.
                        self._warn(f"{stage.name} failed: {exc}")
                        self._record(f"{stage.name} failed: {exc}")
                        continue

                    for message in getattr(result, "messages", []):
                        self._warn(message)
                    status = getattr(result, "status", None)
                    if status is RunStatus.PARTIAL:
                        self._warn(
                            f"{stage.name} completed on cached data because the "
                            "source was unreachable"
                        )
                    elif status is RunStatus.FAILED:
                        self._warn(f"{stage.name} did not complete")

                    with self._lock:
                        self._state.stage_index = index + 1
        except Exception as exc:  # pragma: no cover - defensive
            with self._lock:
                self._state.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._state.running = False
                self._state.stage = None
                self._state.finished_at = utcnow()
                self._state.stage_index = self._state.stage_count


# Module-level singleton; the app has exactly one pipeline.
manager = RefreshManager()
