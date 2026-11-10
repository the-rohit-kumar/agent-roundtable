from __future__ import annotations

from collections import namedtuple
from pathlib import Path

from agent_roundtable.observability import health


def test_disk_space_status_uses_configured_gibibyte_threshold(
    tmp_path: Path, monkeypatch
) -> None:
    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        health.shutil,
        "disk_usage",
        lambda _path: usage(20 * 1024**3, 18 * 1024**3, 2 * 1024**3),
    )
    status = health.disk_space_status(tmp_path, 5)
    assert status.free_gb == 2
    assert status.warning is True
    assert status.threshold_gb == 5
