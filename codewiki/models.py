"""Shared dataclasses and typed structures used across the CodeWiki pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class FileRecord:
    path: str
    lang: str
    size: int
    hash: str
    text: str


@dataclass(slots=True)
class Symbol:
    id: str
    kind: str
    name: str
    path: str
    start_line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    calls: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RepoMap:
    root: Path
    file_tree: list[str]
    language_stats: dict[str, int]
    import_graph: dict[str, list[str]]
    frameworks: list[str]
    entrypoints: list[str]


@dataclass(slots=True)
class Signal:
    type: str
    name: str
    evidence: list[str]


@dataclass(slots=True)
class Snippet:
    text: str
    cite: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)
