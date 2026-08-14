"""Where a packaged desktop build keeps its data.

Running from a checkout, the database and cached downloads sit next to the code
in ``data/``. That cannot work once the app is a macOS ``.app`` bundle: the
bundle is read-only, so the first write fails. This module resolves a writable
per-user location instead, and seeds an editable copy of ``config.yaml`` into it
on first launch so thresholds can be tuned without opening the bundle.

Nothing here changes behaviour for a normal ``uvicorn app.main:app`` run --
:func:`bootstrap` is called only by the desktop launcher.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

COMPANY = "Allstar Partners"
APP_NAME = "FQHC Prospect Intelligence"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    """Directory holding the read-only application resources.

    PyInstaller unpacks datas into ``sys._MEIPASS``; from a checkout this is
    just the project directory.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def user_data_dir(
    *, platform: str | None = None, environ: dict[str, str] | None = None
) -> Path:
    """The per-user writable directory for this application.

    Follows each platform's convention:

    * macOS   -- ``~/Library/Application Support/<Company>/<App>``
    * Windows -- ``%APPDATA%\\<Company>\\<App>``
    * Linux   -- ``$XDG_DATA_HOME/<company>/<app>``, defaulting to
      ``~/.local/share``
    """
    system = platform if platform is not None else sys.platform
    env = environ if environ is not None else os.environ
    home = Path(env.get("HOME") or Path.home())

    if system == "darwin":
        return home / "Library" / "Application Support" / COMPANY / APP_NAME

    if system.startswith("win"):
        base = env.get("APPDATA")
        root = Path(base) if base else home / "AppData" / "Roaming"
        return root / COMPANY / APP_NAME

    xdg = env.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else home / ".local" / "share"
    return root / "allstar-partners" / "fqhc-prospect-intelligence"


def seed_config(data_dir: Path, source: Path | None = None) -> Path:
    """Copy the default config.yaml into the data directory if absent.

    An existing file is never overwritten -- the user's tuned thresholds
    survive every upgrade.
    """
    destination = data_dir / "config.yaml"
    if destination.exists():
        return destination

    origin = source or (bundled_root() / "config.yaml")
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origin, destination)
    return destination


def bootstrap(
    data_dir: Path | None = None, environ: dict[str, str] | None = None
) -> tuple[Path, Path]:
    """Prepare the writable data directory and point the app at it.

    Sets ``FQHC_CONFIG`` and ``FQHC_DATA_DIR`` so that importing ``app.main``
    afterwards picks up the user's copy rather than the bundled one. Returns
    ``(config_path, data_dir)``.

    Must run *before* ``app.main`` is imported, since that module reads its
    configuration at import time.
    """
    env = environ if environ is not None else os.environ
    directory = data_dir or user_data_dir(environ=env)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data" / "raw").mkdir(parents=True, exist_ok=True)

    config_path = seed_config(directory)
    env["FQHC_CONFIG"] = str(config_path)
    env["FQHC_DATA_DIR"] = str(directory)
    return config_path, directory
