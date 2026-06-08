"""Page-to-source mapping used by incremental wiki updates."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


_PAGEMAP = ".codewiki_pagemap.json"


@dataclass(slots=True)
class PageRecord:
    """Reverse index record mapping one page to its source provenance."""

    page: str
    source_files: list[str]
    source_symbols: list[str]
    file_hashes: dict[str, str]
    human_edited: bool = False


def pagemap_path(wiki_root: Path) -> Path:
    return wiki_root / _PAGEMAP


def load_pagemap(wiki_root: Path) -> dict[str, PageRecord]:
    """Load page records keyed by page path."""
    path = pagemap_path(wiki_root)
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    records: dict[str, PageRecord] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            record = _coerce_record(item)
            records[record.page] = record
    elif isinstance(raw, dict):
        # Backward-compatible shape: {"page.md": {...record fields...}}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            value = {"page": key, **value}
            record = _coerce_record(value)
            records[record.page] = record
    return records


def save_pagemap(wiki_root: Path, records: dict[str, PageRecord]) -> None:
    """Persist page records as a stable JSON list."""
    path = pagemap_path(wiki_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        asdict(records[page])
        for page in sorted(records)
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def resolve_affected_pages(
    records: dict[str, PageRecord],
    changed_files: set[str],
) -> set[str]:
    """Return pages whose source provenance intersects changed files."""
    affected: set[str] = set()
    for page, record in records.items():
        if changed_files.intersection(record.source_files):
            affected.add(page)
            continue
        if changed_files.intersection(record.file_hashes.keys()):
            affected.add(page)
    return affected


def _coerce_record(item: dict) -> PageRecord:
    page = str(item.get("page", "")).strip()
    source_files = [
        str(value).strip()
        for value in item.get("source_files", [])
        if str(value).strip()
    ]
    source_symbols = [
        str(value).strip()
        for value in item.get("source_symbols", [])
        if str(value).strip()
    ]

    raw_hashes = item.get("file_hashes", {})
    file_hashes: dict[str, str] = {}
    if isinstance(raw_hashes, dict):
        for key, value in raw_hashes.items():
            k = str(key).strip()
            v = str(value).strip()
            if k and v:
                file_hashes[k] = v

    return PageRecord(
        page=page,
        source_files=source_files,
        source_symbols=source_symbols,
        file_hashes=file_hashes,
        human_edited=bool(item.get("human_edited", False)),
    )
