"""Test fixtures: temporary databases and configs."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Windows: pytest tries to update a 'pytest-current' symlink in %TEMP% at
# session teardown, which fails without Developer Mode / admin privileges.
# Patch it out so the session exits cleanly (all test results are unaffected).
# See https://github.com/pytest-dev/pytest/issues/10407
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    from _pytest import pathlib as _pytest_pathlib

    _orig_cleanup_dead = _pytest_pathlib.cleanup_dead_symlinks

    def _safe_cleanup_dead_symlinks(root: Path) -> None:  # type: ignore[override]
        try:
            _orig_cleanup_dead(root)
        except PermissionError:
            logging.getLogger(__name__).debug(
                "Suppressed PermissionError on pytest-current symlink cleanup (Windows, no symlink privilege)"
            )

    _pytest_pathlib.cleanup_dead_symlinks = _safe_cleanup_dead_symlinks


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "database" / "roundtable.db"


@pytest.fixture
def example_config_path() -> Path:
    from pathlib import Path as _P

    return _P(__file__).resolve().parents[1] / "config" / "config.example.yaml"
