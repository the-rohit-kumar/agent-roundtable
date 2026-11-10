"""Local operational checks that do not contact paid or remote services."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiskSpaceStatus:
    free_gb: float
    warning: bool
    threshold_gb: int


def disk_space_status(path: Path, threshold_gb: int) -> DiskSpaceStatus:
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    free_gb = usage.free / (1024**3)
    return DiskSpaceStatus(
        free_gb=free_gb,
        warning=free_gb < threshold_gb,
        threshold_gb=threshold_gb,
    )
