"""Config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import Config, load_config


def test_ships_with_documented_defaults(config: Config) -> None:
    assert config.app.company == "Allstar Partners"
    assert config.scoring.revenue.sweet_spot_min == 5_000_000
    assert config.scoring.revenue.sweet_spot_max == 50_000_000
    assert config.scoring.sites.minimum == 3
    assert config.scoring.state.target_states == ["IL", "WI", "IN", "MI"]
    assert config.matching.auto_accept_score == 90
    assert config.matching.review_score == 70
    assert config.cache.max_age_days == 30
    assert config.propublica.requests_per_second == 1.0
    assert config.propublica.filings_per_org == 3


def test_relative_paths_resolve_against_project_root(config: Config) -> None:
    assert config.database_file.is_absolute()
    assert config.cache_directory.is_absolute()
    assert config.database_url.startswith("sqlite:////") or config.database_url.startswith(
        "sqlite:///"
    )


def test_state_filter_is_upper_cased(tmp_path: Path, config: Config) -> None:
    raw = yaml.safe_load((config.project_root / "config.yaml").read_text())
    raw["scoring"]["state"]["target_states"] = ["il", " wi "]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    loaded = load_config(path)
    assert loaded.scoring.state.target_states == ["IL", "WI"]


def test_review_threshold_above_auto_accept_is_rejected(
    tmp_path: Path, config: Config
) -> None:
    raw = yaml.safe_load((config.project_root / "config.yaml").read_text())
    raw["matching"]["review_score"] = 95
    raw["matching"]["auto_accept_score"] = 90
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValidationError, match="review_score"):
        load_config(path)


def test_revenue_band_must_be_ordered(tmp_path: Path, config: Config) -> None:
    raw = yaml.safe_load((config.project_root / "config.yaml").read_text())
    raw["scoring"]["revenue"]["sweet_spot_min"] = 60_000_000  # above the max
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValidationError, match="floor <= sweet_spot_min"):
        load_config(path)
