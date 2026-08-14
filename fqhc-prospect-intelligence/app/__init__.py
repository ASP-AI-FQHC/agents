"""FQHC Prospect Intelligence.

This module runs a Python version check before anything else is imported.
Without it, an older interpreter fails deep inside SQLAlchemy's annotation
resolution with a MappedAnnotationError about ``Mapped[str | None]``, which
gives no hint that the real problem is the Python version. macOS ships 3.9 with
the Xcode Command Line Tools, so this is an easy trap to fall into.
"""

from __future__ import annotations

import sys

MINIMUM_PYTHON: tuple[int, int] = (3, 11)


def python_version_error(
    current: tuple[int, int], executable: str
) -> str:
    """The message shown when the interpreter is too old."""
    needed = f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}"
    running = f"{current[0]}.{current[1]}"
    return (
        f"\nFQHC Prospect Intelligence needs Python {needed} or newer.\n"
        f"You are running Python {running} from:\n"
        f"    {executable}\n\n"
        "macOS ships Python 3.9 with the Xcode Command Line Tools, which is\n"
        "too old. To fix it:\n\n"
        "    brew install python@3.12          # or download from python.org\n"
        "    rm -rf .venv\n"
        f"    python3.12 -m venv .venv\n"
        "    source .venv/bin/activate\n"
        "    python --version                  # confirm 3.12\n"
        "    pip install -r requirements.txt\n"
    )


def require_python(
    current: tuple[int, int] | None = None, executable: str | None = None
) -> None:
    """Exit with a readable message if the interpreter is too old."""
    version = current if current is not None else sys.version_info[:2]
    if version >= MINIMUM_PYTHON:
        return
    raise SystemExit(python_version_error(version, executable or sys.executable))


require_python()
