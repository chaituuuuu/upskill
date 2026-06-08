"""Hash-keyed cache used by wiki summarization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SummaryCache:
    """Persist and load summary payloads keyed by source file hash."""

    def __init__(self, cache_dir: Path, enabled: bool = True) -> None:
        self._enabled = enabled
        self._root = cache_dir / "summaries"

    def _path(self, source_hash: str) -> Path:
        return self._root / f"{source_hash}.json"

    def load(self, source_hash: str) -> dict[str, Any] | None:
        if not self._enabled:
            return None

        path = self._path(source_hash)
        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

        if not isinstance(raw, dict):
            return None
        return raw

    def save(self, source_hash: str, payload: dict[str, Any]) -> None:
        if not self._enabled:
            return

        path = self._path(source_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")