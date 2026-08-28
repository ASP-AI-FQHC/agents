"""Daily refreshes, scheduled by the operating system.

The application is not a server -- it is a window somebody opens when they want
to look at prospects. So a "daily pull" cannot live inside it: the app is shut
most of the time. It has to be the OS that wakes something up.

On macOS that is a LaunchAgent: a small plist in ``~/Library/LaunchAgents``
that runs the pipeline at a set hour and, because ``RunAtLoad`` is off and
macOS remembers missed intervals, catches up on the next login if the Mac was
asleep at the appointed time.

    python -m desktop.schedule install --hour 6
    python -m desktop.schedule status
    python -m desktop.schedule remove

Everything is written through :func:`plist_for` and :func:`install`, both of
which take the paths and the runner as arguments, so the file that gets written
can be asserted in a test without a Mac and without touching launchctl.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

LABEL = "partners.allstar.fqhc-prospect-intelligence.refresh"

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[bytes]"]


def _run(argv: Sequence[str]) -> "subprocess.CompletedProcess[bytes]":
    return subprocess.run(list(argv), capture_output=True, check=False)  # noqa: S603


def agents_directory(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents"


def plist_path(home: Path | None = None) -> Path:
    return agents_directory(home) / f"{LABEL}.plist"


def plist_for(
    *,
    python: Path,
    project_root: Path,
    log_directory: Path,
    hour: int,
    minute: int = 0,
    data_dir: Path | None = None,
    config: Path | None = None,
) -> dict:
    """The LaunchAgent definition, as a plain dict ready for plistlib."""
    if not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    if not 0 <= minute <= 59:
        raise ValueError("minute must be between 0 and 59")

    environment = {
        # The pipeline resolves its data directory from these; a LaunchAgent
        # starts with almost no environment, so they are set explicitly rather
        # than inherited.
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
    }
    if data_dir is not None:
        environment["FQHC_DATA_DIR"] = str(data_dir)
    if config is not None:
        environment["FQHC_CONFIG"] = str(config)

    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "pipeline.run", "--quiet"],
        "WorkingDirectory": str(project_root),
        "EnvironmentVariables": environment,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        # Off deliberately: installing the schedule should not kick off a
        # fifteen-minute run then and there.
        "RunAtLoad": False,
        "StandardOutPath": str(log_directory / "daily-refresh.log"),
        "StandardErrorPath": str(log_directory / "daily-refresh.log"),
        # Refreshing is not urgent; let macOS pick a moment that suits it.
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 5,
    }


@dataclass(frozen=True)
class InstallResult:
    path: Path
    hour: int
    minute: int
    loaded: bool
    detail: str = ""

    @property
    def summary(self) -> str:
        when = f"{self.hour:02d}:{self.minute:02d}"
        if self.loaded:
            return f"Daily refresh scheduled for {when}. Definition: {self.path}"
        return (
            f"Wrote {self.path} for {when}, but launchctl did not accept it"
            + (f": {self.detail}" if self.detail else "")
        )


def install(
    *,
    python: Path,
    project_root: Path,
    log_directory: Path,
    hour: int,
    minute: int = 0,
    data_dir: Path | None = None,
    config: Path | None = None,
    home: Path | None = None,
    runner: Runner = _run,
) -> InstallResult:
    """Write the LaunchAgent and ask launchctl to load it."""
    path = plist_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_directory.mkdir(parents=True, exist_ok=True)

    definition = plist_for(
        python=python,
        project_root=project_root,
        log_directory=log_directory,
        hour=hour,
        minute=minute,
        data_dir=data_dir,
        config=config,
    )
    with path.open("wb") as handle:
        plistlib.dump(definition, handle)

    # Replacing an existing schedule means unloading the old one first;
    # bootout on a job that is not loaded is not an error worth reporting.
    target = f"gui/{os.getuid()}"
    runner(["launchctl", "bootout", f"{target}/{LABEL}"])
    completed = runner(["launchctl", "bootstrap", target, str(path)])

    loaded = completed.returncode == 0
    detail = completed.stderr.decode("utf-8", "replace").strip()
    return InstallResult(path=path, hour=hour, minute=minute, loaded=loaded, detail=detail)


def remove(*, home: Path | None = None, runner: Runner = _run) -> bool:
    """Unload and delete the schedule. True if there was one to remove."""
    path = plist_path(home)
    runner(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
    if path.exists():
        path.unlink()
        return True
    return False


def describe(*, home: Path | None = None) -> str:
    """What is scheduled, read back from the file that defines it."""
    path = plist_path(home)
    if not path.exists():
        return "No daily refresh is scheduled."
    try:
        with path.open("rb") as handle:
            definition = plistlib.load(handle)
    except Exception as exc:  # a hand-edited plist
        return f"{path} exists but could not be read: {exc}"

    interval = definition.get("StartCalendarInterval") or {}
    hour = interval.get("Hour", 0)
    minute = interval.get("Minute", 0)
    argv = " ".join(definition.get("ProgramArguments", []))
    return (
        f"Daily refresh at {hour:02d}:{minute:02d}\n"
        f"  Command: {argv}\n"
        f"  Working directory: {definition.get('WorkingDirectory', '?')}\n"
        f"  Log: {definition.get('StandardOutPath', '?')}\n"
        f"  Definition: {path}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m desktop.schedule",
        description="Schedule a daily data refresh (macOS LaunchAgent).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("install", help="Schedule the daily refresh.")
    add.add_argument(
        "--hour", type=int, default=6,
        help="Hour of day, 0-23, in local time (default 6).",
    )
    add.add_argument("--minute", type=int, default=0, help="Minute past the hour.")

    sub.add_parser("status", help="Show what is scheduled.")
    sub.add_parser("remove", help="Remove the daily refresh.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if sys.platform != "darwin" and args.command != "status":
        print(
            "Scheduling is implemented for macOS only. On Linux use cron or a "
            "systemd timer to run `python -m pipeline.run` daily; on Windows "
            "use Task Scheduler.",
            file=sys.stderr,
        )
        return 2

    if args.command == "status":
        print(describe())
        return 0

    project_root = Path(__file__).resolve().parent.parent
    if args.command == "remove":
        print("Daily refresh removed." if remove() else "Nothing was scheduled.")
        return 0

    from app.config import get_config

    config = get_config()
    result = install(
        python=Path(sys.executable),
        project_root=project_root,
        log_directory=config.data_root / "logs",
        hour=args.hour,
        minute=args.minute,
        data_dir=Path(os.environ["FQHC_DATA_DIR"]) if os.environ.get("FQHC_DATA_DIR") else None,
        config=Path(os.environ["FQHC_CONFIG"]) if os.environ.get("FQHC_CONFIG") else None,
    )
    print(result.summary)
    return 0 if result.loaded else 1


if __name__ == "__main__":
    raise SystemExit(main())
