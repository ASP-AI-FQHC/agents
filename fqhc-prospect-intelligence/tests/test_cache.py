"""The 30-day raw-download cache."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from app.models import utcnow
from pipeline.cache import FileCache


def test_store_then_get_roundtrip(tmp_path: Path) -> None:
    cache = FileCache(tmp_path, max_age_days=30)
    entry = cache.store("sites.csv", b"a,b\n1,2\n", source_url="https://example.org/x")

    assert entry.path.exists()
    loaded = cache.get("sites.csv")
    assert loaded is not None
    assert loaded.source_url == "https://example.org/x"
    assert loaded.read_text() == "a,b\n1,2\n"
    assert loaded.size_bytes == 8


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert FileCache(tmp_path).get("absent.csv") is None
    assert FileCache(tmp_path).is_fresh("absent.csv") is False


def test_fresh_within_ttl_and_stale_outside(tmp_path: Path) -> None:
    cache = FileCache(tmp_path, max_age_days=30)
    cache.store("sites.csv", b"data", fetched_at=utcnow() - timedelta(days=29))
    assert cache.is_fresh("sites.csv") is True

    cache.store("sites.csv", b"data", fetched_at=utcnow() - timedelta(days=31))
    assert cache.is_fresh("sites.csv") is False


def test_corrupt_metadata_falls_back_to_file_mtime(tmp_path: Path) -> None:
    cache = FileCache(tmp_path, max_age_days=30)
    cache.store("sites.csv", b"data")
    (tmp_path / "sites.csv.meta.json").write_text("{not json")

    entry = cache.get("sites.csv")
    assert entry is not None
    # The file itself is still usable; only its recorded provenance is lost.
    assert entry.read_text() == "data"
    assert entry.age() < timedelta(minutes=5)


def test_bom_encoded_csv_is_decoded_cleanly(tmp_path: Path) -> None:
    cache = FileCache(tmp_path)
    cache.store("sites.csv", "Name,State\n".encode("utf-8-sig"))
    assert cache.get("sites.csv").read_text().startswith("Name")


def test_store_leaves_no_partial_file(tmp_path: Path) -> None:
    cache = FileCache(tmp_path)
    cache.store("sites.csv", b"payload")
    assert not (tmp_path / "sites.csv.part").exists()
