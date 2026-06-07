"""Symbol-aware code chunking for retrieval."""

from __future__ import annotations

from collections import defaultdict

from codewiki.models import FileRecord, Snippet, Symbol
from codewiki.utils import make_citation


def _line_slice(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines()
    start = max(1, start_line)
    end = min(len(lines), end_line)
    return "\n".join(lines[start - 1 : end])


def chunk_symbols(files: list[FileRecord], symbols: list[Symbol]) -> list[Snippet]:
    """Create retrieval snippets aligned to symbols, with fallback file chunks."""
    by_path: dict[str, FileRecord] = {f.path: f for f in files}
    symbols_by_path: dict[str, list[Symbol]] = defaultdict(list)
    for s in symbols:
        symbols_by_path[s.path].append(s)

    snippets: list[Snippet] = []

    for path, file in by_path.items():
        file_symbols = sorted(symbols_by_path.get(path, []), key=lambda s: s.start_line)
        if file_symbols:
            for s in file_symbols:
                text = _line_slice(file.text, s.start_line, s.end_line)
                cite = make_citation(path, s.start_line, s.end_line)
                snippets.append(
                    Snippet(
                        text=text,
                        cite=cite,
                        score=0.0,
                        metadata={
                            "path": path,
                            "lang": file.lang,
                            "symbol": s.name,
                            "kind": s.kind,
                        },
                    )
                )
            continue

        # Fallback: non-symbol file chunked every ~120 lines
        lines = file.text.splitlines()
        chunk_size = 120
        for i in range(0, len(lines), chunk_size):
            start = i + 1
            end = min(i + chunk_size, len(lines))
            text = "\n".join(lines[i:end])
            cite = make_citation(path, start, end)
            snippets.append(
                Snippet(
                    text=text,
                    cite=cite,
                    score=0.0,
                    metadata={
                        "path": path,
                        "lang": file.lang,
                        "symbol": "",
                        "kind": "chunk",
                    },
                )
            )

    return snippets
