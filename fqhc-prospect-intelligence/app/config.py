"""Typed loader for ``config.yaml``.

The whole application reads its tunable behaviour through :func:`get_config`.
Values are validated by pydantic on load, so a typo in the YAML fails loudly at
startup rather than silently changing how prospects are scored.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

# The project root is the directory containing config.yaml -- i.e. the parent of
# this package. Relative paths in the config are resolved against it.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# FQHC_CONFIG lets the web app and the CLI run against an alternative config
# (a second footprint, a staging database) without editing the shipped file.
DEFAULT_CONFIG_PATH = Path(
    os.environ.get("FQHC_CONFIG") or PROJECT_ROOT / "config.yaml"
)


class AppSettings(BaseModel):
    name: str = "FQHC Prospect Intelligence"
    company: str = "Allstar Partners"
    database_path: Path = Path("data/fqhc.db")


class CacheSettings(BaseModel):
    directory: Path = Path("data/raw")
    max_age_days: int = Field(default=30, ge=0)


class HrsaSettings(BaseModel):
    sites_url: str
    sites_filename: str = "hrsa_service_delivery_sites.csv"
    awardees_url: str
    awardees_filename: str = "hrsa_program_awardees.csv"
    # HRSA renames these downloads between releases. Alternatives are tried in
    # order when the primary URL fails, before falling back to the cache.
    sites_url_fallbacks: list[str] = Field(default_factory=list)
    awardees_url_fallbacks: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=180.0, gt=0)
    # Count only sites HRSA reports as active. Turning this off inflates site
    # counts with closed locations.
    active_sites_only: bool = True


class ProPublicaSettings(BaseModel):
    base_url: str = "https://projects.propublica.org/nonprofits/api/v2"
    requests_per_second: float = Field(default=1.0, gt=0)
    max_retries: int = Field(default=5, ge=0)
    backoff_base_seconds: float = Field(default=2.0, gt=0)
    backoff_max_seconds: float = Field(default=60.0, gt=0)
    timeout_seconds: float = Field(default=30.0, gt=0)
    filings_per_org: int = Field(default=3, ge=1)
    refresh_after_days: int = Field(default=30, ge=0)


class MatchingSettings(BaseModel):
    auto_accept_score: float = Field(default=90.0, ge=0, le=100)
    review_score: float = Field(default=70.0, ge=0, le=100)
    require_state_match: bool = True
    max_candidates: int = Field(default=25, ge=1)
    # If the two best candidates are within this many points of each other, the
    # match goes to human review even when the top score clears auto-accept.
    ambiguity_margin: float = Field(default=3.0, ge=0)
    # How long before an unmatched or rejected organization is searched again.
    refresh_after_days: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def _check_band(self) -> "MatchingSettings":
        if self.review_score > self.auto_accept_score:
            raise ValueError(
                "matching.review_score must be <= matching.auto_accept_score"
            )
        return self


class RevenueScoring(BaseModel):
    sweet_spot_min: float = Field(default=5_000_000, ge=0)
    sweet_spot_max: float = Field(default=50_000_000, ge=0)
    floor: float = Field(default=1_000_000, ge=0)
    ceiling: float = Field(default=150_000_000, ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> "RevenueScoring":
        if not self.floor <= self.sweet_spot_min <= self.sweet_spot_max <= self.ceiling:
            raise ValueError(
                "scoring.revenue requires floor <= sweet_spot_min <= "
                "sweet_spot_max <= ceiling"
            )
        return self


class SitesScoring(BaseModel):
    minimum: int = Field(default=3, ge=1)
    target: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _check_order(self) -> "SitesScoring":
        if self.target < self.minimum:
            raise ValueError("scoring.sites.target must be >= scoring.sites.minimum")
        return self


class StateScoring(BaseModel):
    target_states: list[str] = Field(default_factory=lambda: ["IL", "WI", "IN", "MI"])
    other_state_score: float = Field(default=0.0, ge=0, le=100)

    @model_validator(mode="after")
    def _normalize(self) -> "StateScoring":
        # Compare states case-insensitively everywhere downstream.
        self.target_states = [s.strip().upper() for s in self.target_states]
        return self


class GrantDependenceScoring(BaseModel):
    full_credit_ratio: float = Field(default=0.5, ge=0, le=1)
    zero_credit_ratio: float = Field(default=0.05, ge=0, le=1)

    @model_validator(mode="after")
    def _check_order(self) -> "GrantDependenceScoring":
        if self.zero_credit_ratio >= self.full_credit_ratio:
            raise ValueError(
                "scoring.grant_dependence.zero_credit_ratio must be < full_credit_ratio"
            )
        return self


class ScoringWeights(BaseModel):
    revenue: float = Field(default=35, ge=0)
    sites: float = Field(default=25, ge=0)
    state: float = Field(default=20, ge=0)
    grant_dependence: float = Field(default=20, ge=0)

    @model_validator(mode="after")
    def _check_nonzero(self) -> "ScoringWeights":
        if self.revenue + self.sites + self.state + self.grant_dependence <= 0:
            raise ValueError("scoring.weights must not all be zero")
        return self

    def as_dict(self) -> dict[str, float]:
        return {
            "revenue": self.revenue,
            "sites": self.sites,
            "state": self.state,
            "grant_dependence": self.grant_dependence,
        }


class ScoringSettings(BaseModel):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    revenue: RevenueScoring = Field(default_factory=RevenueScoring)
    sites: SitesScoring = Field(default_factory=SitesScoring)
    state: StateScoring = Field(default_factory=StateScoring)
    grant_dependence: GrantDependenceScoring = Field(
        default_factory=GrantDependenceScoring
    )


class IrsSettings(BaseModel):
    """Form 990 Part VII personnel and contractors, from IRS e-file XML.

    The IRS has moved this data between an S3 bucket and bulk ZIP downloads,
    and the URLs change, so nothing here is assumed to work. Point
    local_directory at a folder of downloaded XML (and optionally the index
    CSVs) and the stage runs with no network at all.
    """

    # Folder of Form 990 XML files, and optionally index*.csv files. Relative
    # paths resolve against the data directory.
    local_directory: Path = Path("data/raw/irs_xml")
    # Try to fetch documents that are not present locally.
    fetch_remote: bool = False
    # Filled in from the IRS index; {object_id} is substituted. Empty by
    # default because there is no working public per-document URL: the old
    # s3://irs-form-990 bucket still exists but was emptied when the IRS moved
    # to bulk ZIP downloads, so shipping it here would look like a working
    # fetch path that silently returns nothing.
    xml_url_template: str = ""
    # Index files mapping EIN to object id. Check the current locations at
    # https://www.irs.gov/charities-non-profits/form-990-series-downloads
    index_urls: list[str] = Field(default_factory=list)
    # How many filings to read per organization; the newest with Part VII wins.
    documents_per_org: int = Field(default=3, ge=1)
    timeout_seconds: float = Field(default=60.0, gt=0)


class WebsiteSettings(BaseModel):
    """Leadership pages on an organization's own website.

    A fallback for the many health centers whose Form 990 is not in the IRS
    bulk download you happen to have. What it produces is weaker evidence than
    a filing -- it is a heuristic read of prose -- so it is stored separately,
    labelled in the UI, and always carries the page it came from.
    """

    enabled: bool = True
    # Only crawl organizations with no Part VII people already on file. Turn
    # off to collect current names even where a filing exists; 990 data lags
    # 12-24 months, so a website is often more current about who holds a post.
    only_when_missing: bool = True
    # Politeness. robots.txt is always honoured; this throttles what remains.
    requests_per_second: float = Field(default=0.5, gt=0)
    timeout_seconds: float = Field(default=20.0, gt=0)
    # Homepage plus this many candidate leadership pages per organization.
    max_pages_per_org: int = Field(default=4, ge=1)
    # Guardrail against a page that fools the extractor into finding hundreds
    # of "people". A real health center leadership page lists tens.
    max_people_per_org: int = Field(default=60, ge=1)
    # Skip organizations crawled more recently than this.
    refresh_after_days: int = Field(default=30, ge=0)
    user_agent: str = (
        "FQHCProspectIntelligence/1.0 (+https://allstar.partners; "
        "contact via allstar.partners)"
    )


class PipelineSettings(BaseModel):
    # Resolve EINs and pull 990s only for organizations in the scoring
    # footprint (scoring.state.target_states). HRSA ingestion is unaffected --
    # the full national universe is still built locally, because that is a
    # parse rather than an API call. Turning this off scores the whole country
    # at the cost of roughly ten times the API traffic.
    restrict_api_to_target_states: bool = True


class UiSettings(BaseModel):
    page_size: int = Field(default=50, ge=1)
    filing_stale_months: int = Field(default=18, ge=0)


class Config(BaseModel):
    """Root configuration object."""

    app: AppSettings = Field(default_factory=AppSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    hrsa: HrsaSettings
    propublica: ProPublicaSettings = Field(default_factory=ProPublicaSettings)
    matching: MatchingSettings = Field(default_factory=MatchingSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    irs: IrsSettings = Field(default_factory=IrsSettings)
    website: WebsiteSettings = Field(default_factory=WebsiteSettings)
    ui: UiSettings = Field(default_factory=UiSettings)

    # Set at load time so relative paths resolve consistently.
    project_root: Path = PROJECT_ROOT

    @property
    def api_states(self) -> list[str] | None:
        """States the API stages are limited to, or None for no limit."""
        if not self.pipeline.restrict_api_to_target_states:
            return None
        return self.scoring.state.target_states or None

    @property
    def data_root(self) -> Path:
        """Directory that relative data paths are resolved against.

        Normally the project root. A packaged desktop build sets FQHC_DATA_DIR
        to a writable per-user directory, because the application bundle it
        runs from is read-only.
        """
        override = os.environ.get("FQHC_DATA_DIR")
        return Path(override) if override else self.project_root

    def resolve(self, path: Path | str) -> Path:
        """Resolve a possibly-relative config path against the data root."""
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.data_root / candidate

    @property
    def database_file(self) -> Path:
        return self.resolve(self.app.database_path)

    @property
    def cache_directory(self) -> Path:
        return self.resolve(self.cache.directory)

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_file}"


def load_config(path: Path | str | None = None) -> Config:
    """Read and validate a config file. Defaults to the project's config.yaml."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle) or {}
    config = Config.model_validate(raw)
    config.project_root = config_path.resolve().parent
    return config


@functools.lru_cache(maxsize=1)
def get_config() -> Config:
    """Process-wide cached configuration."""
    return load_config()
