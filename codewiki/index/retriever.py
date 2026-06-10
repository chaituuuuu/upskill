"""Hybrid retriever interface over the configured local index."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from codewiki.config import CodeWikiConfig
from codewiki.index.store import IndexStore
from codewiki.index.vector_store import VectorStore
from codewiki.llm.budget import Budget
from codewiki.models import Snippet

if TYPE_CHECKING:
    from codewiki.graph.code_graph import CodeGraph


async def retrieve(
    index_dir: Path,
    query: str,
    *,
    cfg: CodeWikiConfig,
    top_k: int = 8,
    enable_graph_scope: bool = True,
    budget: Budget | None = None,
    code_graph: CodeGraph | None = None,
) -> list[Snippet]:
    """Retrieve snippets using BM25 + optional vector fusion and neighborhood boost."""
    store = IndexStore(index_dir)
    bm25_hits = store.search(query, top_k=max(12, top_k * 3))

    corpus = store.load_snippets()
    if not corpus:
        return bm25_hits[:top_k]

    vector_hits: list[Snippet] = []
    if cfg.embedding.enabled:
        vector_store = VectorStore(cfg.run.cache_dir, backend=cfg.embedding.store)
        try:
            vector_hits = await vector_store.search(
                cfg=cfg,
                snippets=corpus,
                query=query,
                top_k=max(12, top_k * 3),
                budget=budget,
            )
        except Exception:
            # Keep BM25 as safe fallback when embeddings are unavailable.
            vector_hits = []

    rankings: list[list[Snippet]] = [bm25_hits]
    if vector_hits:
        rankings.append(vector_hits)
    fused_scores = _rrf_scores(rankings)

    if enable_graph_scope:
        _apply_neighborhood_boost(
            fused_scores,
            corpus,
            bm25_hits,
            code_graph=code_graph,
        )

    by_cite = {item.cite: item for item in corpus}
    for item in bm25_hits:
        by_cite[item.cite] = item
    for item in vector_hits:
        by_cite[item.cite] = item

    ranked = sorted(
        fused_scores.items(),
        key=lambda pair: pair[1],
        reverse=True,
    )

    out: list[Snippet] = []
    for cite, score in ranked:
        source = by_cite.get(cite)
        if source is None:
            continue
        out.append(
            Snippet(
                text=source.text,
                cite=source.cite,
                score=score,
                metadata=source.metadata,
            )
        )
        if len(out) >= top_k:
            break

    return out


def _rrf_scores(rankings: list[list[Snippet]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item.cite] = scores.get(item.cite, 0.0) + (1.0 / (k + rank))
    return scores


def _apply_neighborhood_boost(
    fused_scores: dict[str, float],
    corpus: list[Snippet],
    seeds: list[Snippet],
    *,
    code_graph: CodeGraph | None,
) -> None:
    """Boost snippets in a graph neighborhood seeded from keyword hits."""
    if code_graph is not None:
        _apply_graph_neighborhood_boost(fused_scores, corpus, seeds, code_graph=code_graph)
        return

    _apply_metadata_neighborhood_boost(fused_scores, corpus, seeds)


def _apply_graph_neighborhood_boost(
    fused_scores: dict[str, float],
    corpus: list[Snippet],
    seeds: list[Snippet],
    *,
    code_graph: CodeGraph,
) -> None:
    seed_paths: set[str] = set()
    for item in seeds[:8]:
        path = str(item.metadata.get("path", "")).strip()
        if path:
            seed_paths.add(path)

    if not seed_paths:
        return

    expanded_paths = set(seed_paths)
    for path in list(seed_paths):
        file_id = f"file:{path}"
        for neighbor_id in code_graph.backend.neighbors(file_id):
            if neighbor_id.startswith("file:"):
                expanded_paths.add(neighbor_id.removeprefix("file:"))

    for item in corpus:
        path = str(item.metadata.get("path", "")).strip()
        if path and path in expanded_paths and item.cite in fused_scores:
            fused_scores[item.cite] += 0.15


def _apply_metadata_neighborhood_boost(
    fused_scores: dict[str, float],
    corpus: list[Snippet],
    seeds: list[Snippet],
) -> None:
    """Fallback boost when a graph is unavailable (symbol-sharing approximation)."""
    symbol_to_paths: dict[str, set[str]] = {}
    path_to_symbols: dict[str, set[str]] = {}

    for item in corpus:
        path = str(item.metadata.get("path", "")).strip()
        symbol = str(item.metadata.get("symbol", "")).strip()
        if not path:
            continue

        path_to_symbols.setdefault(path, set())
        if symbol:
            path_to_symbols[path].add(symbol)
            symbol_to_paths.setdefault(symbol, set()).add(path)

    seed_paths: set[str] = set()
    seed_symbols: set[str] = set()
    for item in seeds[:8]:
        path = str(item.metadata.get("path", "")).strip()
        symbol = str(item.metadata.get("symbol", "")).strip()
        if path:
            seed_paths.add(path)
        if symbol:
            seed_symbols.add(symbol)

    neighbor_paths: set[str] = set(seed_paths)
    for symbol in seed_symbols:
        neighbor_paths.update(symbol_to_paths.get(symbol, set()))

    # One-hop expansion over symbol-sharing files.
    expanded = set(neighbor_paths)
    for path in list(neighbor_paths):
        for symbol in path_to_symbols.get(path, set()):
            expanded.update(symbol_to_paths.get(symbol, set()))

    if not expanded:
        return

    for item in corpus:
        path = str(item.metadata.get("path", "")).strip()
        if path and path in expanded and item.cite in fused_scores:
            fused_scores[item.cite] += 0.15
