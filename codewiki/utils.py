"""General helper functions used across modules."""

from __future__ import annotations

import hashlib
from pathlib import Path


_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".cs": "csharp",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".sql": "sql",
    ".sh": "shell",
}


def detect_language(path: Path) -> str:
    return _LANG_MAP.get(path.suffix.lower(), "text")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def make_citation(path: str, start_line: int, end_line: int) -> str:
    return f"{path}:L{start_line}-L{end_line}"


def safe_slug(name: str) -> str:
    out = []
    for ch in name.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_", "/", "."}:
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "page"
