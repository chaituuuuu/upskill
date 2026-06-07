"""Hybrid retriever interface over the configured local index."""

from __future__ import annotations

from pathlib import Path

from codewiki.index.store import IndexStore
from codewiki.models import Snippet


def retrieve(index_dir: Path, query: str, top_k: int = 8) -> list[Snippet]:
    store = IndexStore(index_dir)
    return store.search(query, top_k=top_k)
