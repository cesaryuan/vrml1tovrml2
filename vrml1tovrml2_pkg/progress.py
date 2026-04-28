"""Optional progress reporting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import sys
import time


@dataclass(slots=True)
class SimpleByteProgress:
    """Provide a lightweight stderr progress indicator when tqdm is unavailable."""

    total: int
    label: str
    current: int = 0
    last_render_time: float = 0.0
    closed: bool = False

    def update(self, amount: int) -> None:
        """Accumulate progress and occasionally refresh the stderr status line."""

        self.current += amount
        now = time.monotonic()
        if self.current < self.total and now - self.last_render_time < 0.2:
            return
        self.last_render_time = now
        self._render(final=self.current >= self.total)

    def close(self) -> None:
        """Finish the progress line if it was not already completed."""

        if self.closed:
            return
        self._render(final=True)
        self.closed = True

    def _render(self, final: bool) -> None:
        """Render one human-readable progress line to stderr."""

        total = max(self.total, 1)
        percent = min(100.0, (self.current / total) * 100.0)
        current_mb = self.current / (1024 * 1024)
        total_mb = self.total / (1024 * 1024)
        line = f"\r[{self.label}] {percent:6.2f}% {current_mb:8.1f}/{total_mb:8.1f} MiB"
        if final:
            line += "\n"
        sys.stderr.write(line)
        sys.stderr.flush()


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
        return SimpleByteProgress(total=path.stat().st_size, label=path.name)
    return tqdm(total=path.stat().st_size, unit="B", unit_scale=True, desc=path.name)
