"""Impact analysis: find all files/pages that depend on a changed symbol or file."""

from __future__ import annotations

from pathlib import Path

from codewiki.graph.code_graph import CodeGraph


def impact(
    target: str,
    code_graph: CodeGraph,
    wiki_root: Path | None = None,
) -> dict:
    """Return files and wiki pages that transitively depend on *target*.

    Args:
        target: A file path (relative), a symbol id (``path::name:line``), or
                a ``file:`` / ``external:`` node id.
        code_graph: The in-process code graph built by :class:`CodeGraph`.
        wiki_root: Optional path to the wiki output directory.  When provided
                   and a ``pagemap.json`` exists (W5), affected pages are
                   resolved from it.  Otherwise ``affected_pages`` is empty.

    Returns a dict with keys:
        ``target``, ``affected_files``, ``affected_pages``.
    """
    ancestors = code_graph.impact_analysis(target)

    # Strip the "file:" prefix for display
    affected_files: list[str] = sorted(
        node_id.removeprefix("file:") if node_id.startswith("file:") else node_id
        for node_id in ancestors
        if not node_id.startswith("external:")
    )

    affected_pages: list[str] = []
    if wiki_root is not None:
        pagemap_path = wiki_root / ".codewiki_pagemap.json"
        if pagemap_path.exists():
            import json
            try:
                records = json.loads(pagemap_path.read_text(encoding="utf-8"))
                af_set = set(affected_files)
                for record in records:
                    if any(sf in af_set for sf in record.get("source_files", [])):
                        affected_pages.append(record["page"])
            except Exception:
                pass

    return {
        "target": target,
        "affected_files": affected_files,
        "affected_pages": affected_pages,
    }
