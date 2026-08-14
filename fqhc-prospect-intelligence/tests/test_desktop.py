"""Desktop packaging: writable data locations and the server lifecycle.

The window itself needs a display and a platform webview, so it is not covered
here; everything underneath it is.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI

from app.config import load_config
from desktop.paths import (
    bootstrap,
    bundled_root,
    other_database,
    seed_config,
    user_data_dir,
)
from desktop.server import ServerController, free_port


# ---------------------------------------------------------------------------
# Per-platform data locations
# ---------------------------------------------------------------------------


def test_macos_uses_application_support(tmp_path: Path) -> None:
    directory = user_data_dir(platform="darwin", environ={"HOME": str(tmp_path)})
    assert directory == (
        tmp_path
        / "Library"
        / "Application Support"
        / "Allstar Partners"
        / "FQHC Prospect Intelligence"
    )


def test_windows_uses_appdata(tmp_path: Path) -> None:
    directory = user_data_dir(
        platform="win32", environ={"HOME": str(tmp_path), "APPDATA": str(tmp_path / "AD")}
    )
    assert directory == tmp_path / "AD" / "Allstar Partners" / "FQHC Prospect Intelligence"


def test_windows_without_appdata_falls_back_to_roaming(tmp_path: Path) -> None:
    directory = user_data_dir(platform="win32", environ={"HOME": str(tmp_path)})
    assert directory.parts[-3:] == (
        "Roaming",
        "Allstar Partners",
        "FQHC Prospect Intelligence",
    )


def test_linux_honours_xdg_data_home(tmp_path: Path) -> None:
    directory = user_data_dir(
        platform="linux", environ={"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "x")}
    )
    assert directory == tmp_path / "x" / "allstar-partners" / "fqhc-prospect-intelligence"


def test_linux_default_is_local_share(tmp_path: Path) -> None:
    directory = user_data_dir(platform="linux", environ={"HOME": str(tmp_path)})
    assert directory == (
        tmp_path / ".local" / "share" / "allstar-partners" / "fqhc-prospect-intelligence"
    )


# ---------------------------------------------------------------------------
# Config seeding
# ---------------------------------------------------------------------------


def test_config_is_seeded_on_first_run(tmp_path: Path) -> None:
    source = tmp_path / "bundled.yaml"
    source.write_text("app:\n  name: Test\n")

    seeded = seed_config(tmp_path / "data", source=source)

    assert seeded.exists()
    assert seeded.read_text() == "app:\n  name: Test\n"


def test_existing_config_is_never_overwritten(tmp_path: Path) -> None:
    """A user's tuned thresholds must survive an upgrade."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "config.yaml").write_text("app:\n  name: Mine\n")

    source = tmp_path / "bundled.yaml"
    source.write_text("app:\n  name: Shipped\n")

    seed_config(data_dir, source=source)
    assert (data_dir / "config.yaml").read_text() == "app:\n  name: Mine\n"


def test_bootstrap_prepares_the_directory_and_environment(tmp_path: Path) -> None:
    environ: dict[str, str] = {"HOME": str(tmp_path)}
    data_dir = tmp_path / "appdata"

    config_path, resolved = bootstrap(data_dir=data_dir, environ=environ)

    assert resolved == data_dir
    assert config_path == data_dir / "config.yaml"
    assert (data_dir / "data" / "raw").is_dir()
    assert environ["FQHC_CONFIG"] == str(config_path)
    assert environ["FQHC_DATA_DIR"] == str(data_dir)


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    environ: dict[str, str] = {"HOME": str(tmp_path)}
    data_dir = tmp_path / "appdata"

    bootstrap(data_dir=data_dir, environ=environ)
    (data_dir / "config.yaml").write_text("app:\n  name: Edited\n")
    bootstrap(data_dir=data_dir, environ=environ)

    assert (data_dir / "config.yaml").read_text() == "app:\n  name: Edited\n"


def test_a_source_checkout_uses_the_checkout(monkeypatch) -> None:
    """The single most confusing failure this project has had: the pipeline
    wrote a database next to the code, the window opened a different one in
    Application Support, and every page looked empty."""
    monkeypatch.setattr("desktop.paths.is_frozen", lambda: False)
    environ: dict[str, str] = {"HOME": "/nowhere"}

    _config_path, resolved = bootstrap(environ=environ)

    assert resolved == bundled_root()
    assert "Application Support" not in str(resolved)


def test_a_packaged_build_still_uses_the_per_user_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """A bundle is read-only, so this half must not change."""
    monkeypatch.setattr("desktop.paths.is_frozen", lambda: True)
    monkeypatch.setattr("desktop.paths.seed_config", lambda directory: directory / "config.yaml")
    environ: dict[str, str] = {"HOME": str(tmp_path)}

    _config_path, resolved = bootstrap(environ=environ)

    assert resolved == user_data_dir(environ=environ)


def test_an_explicit_override_wins_over_both(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("desktop.paths.is_frozen", lambda: True)
    chosen = tmp_path / "chosen"
    environ: dict[str, str] = {"HOME": str(tmp_path), "FQHC_DATA_DIR": str(chosen)}

    _config_path, resolved = bootstrap(environ=environ)

    assert resolved == chosen


def test_a_database_in_the_other_location_is_reported(tmp_path: Path, monkeypatch) -> None:
    """Both locations are legitimate, so the one not in use is worth naming."""
    other = tmp_path / "appdata" / "data"
    other.mkdir(parents=True)
    (other / "fqhc.db").write_bytes(b"not empty")
    monkeypatch.setattr(
        "desktop.paths.user_data_dir", lambda **_kwargs: tmp_path / "appdata"
    )

    active = tmp_path / "checkout" / "data" / "fqhc.db"
    assert other_database(active, environ={"HOME": str(tmp_path)}) == other / "fqhc.db"


def test_no_report_when_only_one_database_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "desktop.paths.user_data_dir", lambda **_kwargs: tmp_path / "appdata"
    )
    monkeypatch.setattr("desktop.paths.bundled_root", lambda: tmp_path / "checkout")

    active = tmp_path / "checkout" / "data" / "fqhc.db"
    assert other_database(active, environ={"HOME": str(tmp_path)}) is None


# ---------------------------------------------------------------------------
# The database lands somewhere writable
# ---------------------------------------------------------------------------


def test_data_root_follows_the_environment_override(tmp_path: Path, monkeypatch) -> None:
    """In a bundle the install directory is read-only, so relative data paths
    must resolve against the per-user directory instead."""
    project = tmp_path / "bundle"
    project.mkdir()
    raw = yaml.safe_load((Path("config.yaml")).read_text())
    (project / "config.yaml").write_text(yaml.safe_dump(raw))

    writable = tmp_path / "userdata"
    monkeypatch.setenv("FQHC_DATA_DIR", str(writable))

    config = load_config(project / "config.yaml")

    assert config.data_root == writable
    assert config.database_file == writable / "data" / "fqhc.db"
    assert config.cache_directory == writable / "data" / "raw"


def test_without_the_override_paths_stay_next_to_the_project(tmp_path: Path) -> None:
    os.environ.pop("FQHC_DATA_DIR", None)
    config = load_config(Path("config.yaml"))
    assert config.data_root == config.project_root
    assert config.database_file.parent.parent == config.project_root


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def test_free_port_returns_a_usable_port() -> None:
    port = free_port()
    assert 1024 < port < 65536
    assert free_port() != 0


@pytest.fixture()
def tiny_app() -> FastAPI:
    application = FastAPI()

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return application


def test_server_starts_serves_and_stops(tiny_app: FastAPI) -> None:
    server = ServerController(tiny_app)
    server.start()
    try:
        assert server.wait_until_ready(timeout=20) is True
        assert httpx.get(f"{server.url}/healthz", timeout=5).json()["status"] == "ok"
    finally:
        server.stop()

    assert server.is_running is False
    # The port is released, so reopening the app cannot collide with itself.
    with httpx.Client() as client:
        with pytest.raises(httpx.HTTPError):
            client.get(f"{server.url}/healthz", timeout=2)


def test_server_context_manager_stops_on_exit(tiny_app: FastAPI) -> None:
    with ServerController(tiny_app) as server:
        assert server.wait_until_ready(timeout=20) is True
    assert server.is_running is False


def test_each_controller_picks_its_own_port(tiny_app: FastAPI) -> None:
    first = ServerController(tiny_app)
    second = ServerController(tiny_app)
    assert first.port != second.port


def test_wait_until_ready_gives_up_rather_than_hanging() -> None:
    """A server that never comes up must not freeze the launcher."""
    server = ServerController(FastAPI(), port=free_port())
    # Never started, so nothing will ever answer.
    assert server.wait_until_ready(timeout=1.0, interval=0.05) is False
