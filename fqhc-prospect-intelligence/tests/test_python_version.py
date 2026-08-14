"""The interpreter version guard.

macOS ships Python 3.9 with the Xcode Command Line Tools. Without this check
the failure is a MappedAnnotationError deep inside SQLAlchemy that says nothing
about Python versions.
"""

from __future__ import annotations

import pytest

from app import MINIMUM_PYTHON, python_version_error, require_python


def test_current_interpreter_is_supported() -> None:
    require_python()  # must not raise in a correctly set up environment


@pytest.mark.parametrize("version", [(3, 9), (3, 10), (2, 7)])
def test_older_interpreters_are_rejected(version: tuple[int, int]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        require_python(version, "/usr/bin/python3")

    message = str(exit_info.value)
    assert "needs Python 3.11 or newer" in message
    assert f"Python {version[0]}.{version[1]}" in message
    # The path matters: on a Mac there are usually several interpreters.
    assert "/usr/bin/python3" in message


def test_message_gives_a_way_out() -> None:
    message = python_version_error((3, 9), "/usr/bin/python3")
    assert "brew install python@3.12" in message
    assert "rm -rf .venv" in message
    assert "Xcode Command Line Tools" in message


@pytest.mark.parametrize("version", [MINIMUM_PYTHON, (3, 12), (4, 0)])
def test_supported_interpreters_pass(version: tuple[int, int]) -> None:
    require_python(version, "/usr/bin/python3")
