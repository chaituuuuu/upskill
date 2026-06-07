"""Filesystem walker with include/exclude filters and basic binary skipping."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.models import FileRecord
from codewiki.utils import detect_language, sha256_text


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(1024)
        return b"\x00" in head
    except OSError:
        return True


def _match_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def walk_source(root: Path, cfg: CodeWikiConfig) -> list[FileRecord]:
    """Read source files from a repo root into normalized FileRecord objects."""
    out: list[FileRecord] = []
    max_size = cfg.ingest.max_file_size_kb * 1024
    wiki_dir = cfg.wiki.output_dir.as_posix().strip("./")
    cache_dir = cfg.run.cache_dir.as_posix().strip("./")

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        rel = path.relative_to(root).as_posix()

        # Avoid recursive ingestion of generated artifacts when source is repo root.
        if wiki_dir and (rel == wiki_dir or rel.startswith(wiki_dir + "/")):
            continue
        if cache_dir and (rel == cache_dir or rel.startswith(cache_dir + "/")):
            continue

        if cfg.ingest.exclude_globs and _match_any(rel, cfg.ingest.exclude_globs):
            continue
        if cfg.ingest.include_globs and not _match_any(rel, cfg.ingest.include_globs):
            continue

        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_size:
            continue
        if cfg.ingest.skip_binary and _is_binary(path):
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        out.append(
            FileRecord(
                path=rel,
                lang=detect_language(path),
                size=size,
                hash=sha256_text(text),
                text=text,
            )
        )

        if cfg.run.max_files is not None and len(out) >= cfg.run.max_files:
            break

    return out
