"""Base contracts for pluggable CodeWiki analysis lenses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Callable

from codewiki.models import FileRecord, RepoMap, Signal, Symbol

if TYPE_CHECKING:
    from codewiki.config import CodeWikiConfig
    from codewiki.graph.code_graph import CodeGraph
    from codewiki.wiki.summarizer import SummaryBundle

SignalDetector = Callable[[list[FileRecord], list[Symbol]], list[Signal]]


@dataclass(slots=True)
class LensPage:
    """Additional page emitted by a lens."""

    rel_path: str
    title: str
    page_type: str
    audience: str
    summary: str
    sections: list[tuple[str, str]]
    sources: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: str | None = None


class BaseLens:
    """Default no-op lens; specialized lenses override selective hooks."""

    name = "business"

    def system_prompt_addendum(self) -> str:
        return ""

    def extra_signal_detectors(self) -> list[SignalDetector]:
        return []

    def page_templates(self) -> list[str]:
        return []

    def scoring(
        self,
        signals: list[Signal],
        graph: CodeGraph | None,
    ) -> dict[str, float]:
        return {}

    def extra_pages(
        self,
        *,
        source_root: Path,
        cfg: CodeWikiConfig,
        files: list[FileRecord],
        symbols: list[Symbol],
        repo_map: RepoMap,
        signals: list[Signal],
        code_graph: CodeGraph | None,
        summary_bundle: SummaryBundle | None,
    ) -> list[LensPage]:
        return []


class BusinessLens(BaseLens):
    """Default business lens (existing behavior)."""

    name = "business"
