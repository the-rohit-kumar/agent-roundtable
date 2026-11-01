"""Test fixtures: temporary databases and configs."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "database" / "roundtable.db"


@pytest.fixture
def example_config_path() -> Path:
    from pathlib import Path as _P

    return _P(__file__).resolve().parents[1] / "config" / "config.example.yaml"
