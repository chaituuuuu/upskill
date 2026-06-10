"""Express signal pack placeholder for future framework-specific extraction."""

from __future__ import annotations

from codewiki.models import FileRecord, Signal, Symbol


def detect_express_signals(files: list[FileRecord], symbols: list[Symbol]) -> list[Signal]:
    """Placeholder detector; returns no framework-specific signals yet."""
    del files, symbols
    return []
