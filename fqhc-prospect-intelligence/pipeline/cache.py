"""Timestamped on-disk cache for raw source downloads.

Each cached file ``foo.csv`` is accompanied by ``foo.csv.meta.json`` recording
when it was fetched and where from. That timestamp is what lets the pipeline
skip re-downloading a file younger than the configured TTL, and what the UI
displays when it is running on cached data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import utcnow


@dataclass(frozen=True)
class CacheEntry:
    """A file present in the cache, plus its provenance."""

    path: Path
    fetched_at: datetime
    source_url: str | None
    size_bytes: int

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or utcnow()) - self.fetched_at

    def is_fresh(self, max_age_days: int, now: datetime | None = None) -> bool:
        return self.age(now) < timedelta(days=max_age_days)

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()

    def read_text(self, encoding: str = "utf-8-sig") -> str:
        # HRSA CSVs are frequently UTF-8 with a BOM; utf-8-sig strips it when
        # present and is a no-op otherwise. Latin-1 is the documented fallback.
        try:
            return self.path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            return self.path.read_text(encoding="latin-1")


class FileCache:
    """Filesystem cache rooted at a directory, with a day-granularity TTL."""

    def __init__(self, directory: Path, max_age_days: int = 30) -> None:
        self.directory = Path(directory)
        self.max_age_days = max_age_days
        self.directory.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------------

    def path_for(self, filename: str) -> Path:
        return self.directory / filename

    def _meta_path(self, filename: str) -> Path:
        return self.directory / f"{filename}.meta.json"

    # -- reads ---------------------------------------------------------------

    def get(self, filename: str) -> CacheEntry | None:
        """Return the cache entry for ``filename``, or None if absent."""
        path = self.path_for(filename)
        if not path.exists():
            return None

        meta_path = self._meta_path(filename)
        source_url: str | None = None
        fetched_at: datetime | None = None

        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                source_url = meta.get("source_url")
                raw_ts = meta.get("fetched_at")
                if raw_ts:
                    fetched_at = datetime.fromisoformat(raw_ts)
            except (json.JSONDecodeError, ValueError):
                # A corrupt sidecar must not hide an otherwise usable file; fall
                # back to the filesystem mtime below.
                fetched_at = None

        if fetched_at is None:
            fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)

        return CacheEntry(
            path=path,
            fetched_at=fetched_at,
            source_url=source_url,
            size_bytes=path.stat().st_size,
        )

    def is_fresh(self, filename: str, now: datetime | None = None) -> bool:
        """True when a cached copy exists and is younger than the TTL."""
        entry = self.get(filename)
        return entry is not None and entry.is_fresh(self.max_age_days, now)

    # -- writes --------------------------------------------------------------

    def store(
        self,
        filename: str,
        content: bytes,
        source_url: str | None = None,
        fetched_at: datetime | None = None,
    ) -> CacheEntry:
        """Write content plus its metadata sidecar, and return the entry."""
        path = self.path_for(filename)
        stamp = fetched_at or utcnow()

        # Write to a temp file first so an interrupted download never leaves a
        # truncated CSV that later looks like a valid cache hit.
        tmp_path = path.with_suffix(path.suffix + ".part")
        tmp_path.write_bytes(content)
        tmp_path.replace(path)

        self._meta_path(filename).write_text(
            json.dumps(
                {
                    "source_url": source_url,
                    "fetched_at": stamp.isoformat(),
                    "size_bytes": len(content),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return CacheEntry(
            path=path,
            fetched_at=stamp,
            source_url=source_url,
            size_bytes=len(content),
        )
