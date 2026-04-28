"""Optional progress reporting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class NullProgress:
    """Fallback progress reporter used when tqdm is unavailable or disabled."""

    total: int | None = None

    def update(self, _amount: int) -> None:
        """Ignore progress updates."""

    def close(self) -> None:
        """Ignore close requests."""


def create_byte_progress(path: Path, enabled: bool = True) -> Any:
    """Create a byte-oriented progress bar when tqdm is available."""

    if not enabled:
        return NullProgress(total=path.stat().st_size)
    try:
        from tqdm import tqdm  # type: ignore
    except ImportError:
        return NullProgress(total=path.stat().st_size)
    return tqdm(total=path.stat().st_size, unit="B", unit_scale=True, desc=path.name)
