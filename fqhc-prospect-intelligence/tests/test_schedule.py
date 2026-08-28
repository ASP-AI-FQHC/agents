"""The daily refresh schedule.

launchctl is not available here, so it is injected: every test asserts on the
plist that gets written and the commands that would have been run. That is the
whole surface -- macOS reads the file, and the file is what has to be right.
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from desktop.schedule import (
    LABEL,
    describe,
    install,
    plist_for,
    plist_path,
    remove,
)


def fake_runner(returncode: int = 0, stderr: bytes = b""):
    """A launchctl stand-in that records what it was asked to do."""
    calls: list[list[str]] = []

    def run(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, b"", stderr)

    return run, calls


@pytest.fixture()
def paths(tmp_path: Path) -> dict:
    return {
        "python": tmp_path / "venv" / "bin" / "python",
        "project_root": tmp_path / "checkout",
        "log_directory": tmp_path / "data" / "logs",
        "home": tmp_path / "home",
    }


# ---------------------------------------------------------------------------
# The definition
# ---------------------------------------------------------------------------


def test_the_schedule_runs_the_pipeline_at_the_chosen_hour(paths) -> None:
    definition = plist_for(
        python=paths["python"],
        project_root=paths["project_root"],
        log_directory=paths["log_directory"],
        hour=6,
    )

    assert definition["Label"] == LABEL
    assert definition["ProgramArguments"][1:] == ["-m", "pipeline.run", "--quiet"]
    assert definition["StartCalendarInterval"] == {"Hour": 6, "Minute": 0}
    assert definition["WorkingDirectory"] == str(paths["project_root"])


def test_installing_does_not_start_a_run(paths) -> None:
    """RunAtLoad would kick off a fifteen-minute pipeline the moment somebody
    ticks the box in the installer."""
    definition = plist_for(
        python=paths["python"], project_root=paths["project_root"],
        log_directory=paths["log_directory"], hour=6,
    )
    assert definition["RunAtLoad"] is False


def test_the_refresh_yields_to_whatever_else_is_running(paths) -> None:
    definition = plist_for(
        python=paths["python"], project_root=paths["project_root"],
        log_directory=paths["log_directory"], hour=6,
    )
    assert definition["ProcessType"] == "Background"
    assert definition["LowPriorityIO"] is True


def test_the_data_directory_is_passed_explicitly(paths, tmp_path: Path) -> None:
    """A LaunchAgent starts with almost no environment, so a scheduled run
    would otherwise build a different database than the app reads."""
    definition = plist_for(
        python=paths["python"], project_root=paths["project_root"],
        log_directory=paths["log_directory"], hour=6,
        data_dir=tmp_path / "userdata", config=tmp_path / "config.yaml",
    )
    env = definition["EnvironmentVariables"]
    assert env["FQHC_DATA_DIR"] == str(tmp_path / "userdata")
    assert env["FQHC_CONFIG"] == str(tmp_path / "config.yaml")
    assert "PATH" in env


def test_output_goes_to_a_log_file(paths) -> None:
    definition = plist_for(
        python=paths["python"], project_root=paths["project_root"],
        log_directory=paths["log_directory"], hour=6,
    )
    expected = str(paths["log_directory"] / "daily-refresh.log")
    assert definition["StandardOutPath"] == expected
    assert definition["StandardErrorPath"] == expected


@pytest.mark.parametrize("hour,minute", [(-1, 0), (24, 0), (6, 60), (6, -1)])
def test_an_impossible_time_is_rejected(paths, hour, minute) -> None:
    with pytest.raises(ValueError):
        plist_for(
            python=paths["python"], project_root=paths["project_root"],
            log_directory=paths["log_directory"], hour=hour, minute=minute,
        )


# ---------------------------------------------------------------------------
# Installing and removing
# ---------------------------------------------------------------------------


def test_install_writes_a_readable_plist(paths) -> None:
    runner, _ = fake_runner()
    result = install(**paths, hour=7, minute=30, runner=runner)

    assert result.loaded
    with result.path.open("rb") as handle:
        written = plistlib.load(handle)
    assert written["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert paths["log_directory"].is_dir()


def test_install_replaces_an_existing_schedule(paths) -> None:
    """Bootstrapping over a loaded job fails, so the old one is booted out
    first -- otherwise changing the hour silently keeps the old time."""
    runner, calls = fake_runner()
    install(**paths, hour=6, runner=runner)

    assert calls[0][:2] == ["launchctl", "bootout"]
    assert calls[1][:2] == ["launchctl", "bootstrap"]


def test_a_rejected_schedule_is_reported_not_swallowed(paths) -> None:
    runner, _ = fake_runner(returncode=1, stderr=b"Load failed: 5: Input/output error")
    result = install(**paths, hour=6, runner=runner)

    assert not result.loaded
    assert "launchctl did not accept it" in result.summary
    assert "Input/output error" in result.summary


def test_remove_deletes_the_definition(paths) -> None:
    runner, _ = fake_runner()
    install(**paths, hour=6, runner=runner)

    assert remove(home=paths["home"], runner=runner) is True
    assert not plist_path(paths["home"]).exists()


def test_removing_nothing_says_so(paths) -> None:
    runner, _ = fake_runner()
    assert remove(home=paths["home"], runner=runner) is False


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_when_nothing_is_scheduled(paths) -> None:
    assert "No daily refresh" in describe(home=paths["home"])


def test_status_reads_the_time_back(paths) -> None:
    runner, _ = fake_runner()
    install(**paths, hour=6, minute=15, runner=runner)

    text = describe(home=paths["home"])
    assert "06:15" in text
    assert "pipeline.run" in text
    assert "daily-refresh.log" in text


def test_a_corrupt_definition_is_reported(paths) -> None:
    path = plist_path(paths["home"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not a plist")

    assert "could not be read" in describe(home=paths["home"])
