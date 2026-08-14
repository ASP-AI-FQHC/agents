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

    **Run from a checkout, this uses the checkout itself.** Only a packaged
    build needs the per-user directory, because only a packaged build is
    read-only. Redirecting a source run there too split the data in half:
    ``python -m pipeline.run`` built a database next to the code while
    ``python -m desktop.main`` opened a different one somewhere else, and the
    window showed none of the work the pipeline had just done.
    """
    env = environ if environ is not None else os.environ

    if data_dir is not None:
        directory = Path(data_dir)
    elif env.get("FQHC_DATA_DIR"):
        # An explicit override always wins, however the app was started.
        directory = Path(env["FQHC_DATA_DIR"])
    elif is_frozen():
        directory = user_data_dir(environ=env)
    else:
        directory = bundled_root()

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data" / "raw").mkdir(parents=True, exist_ok=True)

    config_path = Path(env["FQHC_CONFIG"]) if env.get("FQHC_CONFIG") else seed_config(directory)
    env["FQHC_CONFIG"] = str(config_path)
    env["FQHC_DATA_DIR"] = str(directory)
    return config_path, directory


def other_database(active: Path, *, environ: dict[str, str] | None = None) -> Path | None:
    """A populated database in the location this run is *not* using.

    Both locations are legitimate -- the packaged app has to write to the
    per-user directory, a checkout writes next to the code -- so a database in
    the other one is worth pointing at rather than silently ignoring.
    """
    env = environ if environ is not None else os.environ
    candidates = [
        user_data_dir(environ=env) / "data" / "fqhc.db",
        bundled_root() / "data" / "fqhc.db",
    ]
    for candidate in candidates:
        try:
            if candidate != active and candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None
